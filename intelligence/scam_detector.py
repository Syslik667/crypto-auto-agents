"""Read-only evidence keyed by chain ID and contract. No address-only fallbacks."""
import base64
import os
import re
from urllib.parse import urlencode

from .net import failure, number, read_json

NETWORKS = {"1": "ethereum", "8453": "base", "42161": "arbitrum"}
LOW = "Низкий обнаруженный риск"
REVIEW = "Требует проверки"
HIGH = "Высокий риск"
UNKNOWN = "Недостаточно данных"
FLAGS = {
    "is_honeypot": "признаки honeypot", "cannot_sell_all": "ограничение продажи",
    "owner_change_balance": "владелец может менять балансы", "hidden_owner": "скрытый владелец",
    "can_take_back_ownership": "возможно восстановить полномочия",
    "is_mintable": "возможна дополнительная эмиссия", "is_proxy": "обновляемый прокси",
    "transfer_pausable": "можно приостановить переводы", "is_blacklisted": "возможна блокировка адресов",
}
SEVERE = {"is_honeypot", "owner_change_balance", "cannot_sell_all"}


def complaints(chain, address, month_cache):
    key = os.environ.get("CHAINABUSE_API_KEY")
    url = "https://api.chainabuse.com/v0/reports?" + urlencode(
        dict(address=address, chain="ETH", includePrivate="false", perPage=1))
    if not key or chain != "1":
        return None, "не подключён для этой сети"
    if address in month_cache:
        return month_cache[address], "кэш текущего месяца"
    if len(month_cache) >= 10:
        return None, "исчерпан месячный лимит"
    month_cache[address] = None  # Failed attempts also spend the local quota.
    auth = base64.b64encode(f"{key}:{key}".encode()).decode()
    obj = read_json(url, headers={"Authorization": "Basic " + auth})
    rows = obj.get("reports") if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        raise ValueError("Unexpected complaints response")
    month_cache[address] = bool(rows)
    return bool(rows), "публичные жалобы, выборка до одной"


def valid_pair(chain, address):
    return chain in NETWORKS and bool(re.fullmatch(r"0x[0-9a-f]{40}", address))


def inspect(chain, address, reference, month_cache):
    if not valid_pair(chain, address):
        raise ValueError("Unsupported chain/contract")
    network = NETWORKS[chain]
    sources = {
        "GoPlus": f"https://api.gopluslabs.io/api/v1/token_security/{chain}?contract_addresses={address}",
        "DEX Screener": f"https://api.dexscreener.com/token-pairs/v1/{network}/{address}",
        "DeFiLlama": f"https://coins.llama.fi/prices/current/{network}:{address}",
        "Chainabuse": "https://www.chainabuse.com/address/" + address,
    }
    if reference:
        sources["Публикация с адресом"] = reference
    flags, alerts, gaps, details = [], [], [], []
    try:
        response = read_json(sources["GoPlus"])
        if str(response.get("code")) != "1":
            raise ValueError("Provider error")
        row = response["result"][address]
        for field, description in FLAGS.items():
            if str(row.get(field)) == "1":
                (flags if field in SEVERE else alerts).append(description)
            elif str(row.get(field)) != "0":
                gaps.append("GoPlus · " + description + " неизвестно")
        if str(row.get("is_open_source")) != "1":
            gaps.append("GoPlus · открытый код не подтверждён")
        sell_tax = number(row.get("sell_tax"))
        if sell_tax is None or not 0 <= sell_tax <= 1:
            gaps.append("GoPlus · налог продажи неизвестен")
        elif sell_tax >= 0.5:
            flags.append(f"налог при продаже {sell_tax:.0%}")
        elif sell_tax > 0.1:
            alerts.append(f"налог при продаже {sell_tax:.0%}")
        holders = [number(h.get("percent")) for h in row.get("holders") or []]
        if holders and all(n is not None and 0 <= n <= 1 for n in holders):
            share = max(holders)
            details.append(f"GoPlus · крупнейшая доля в выборке {share:.1%} (включая контракты)")
            if share >= 0.2:
                alerts.append("один адрес держит ≥20%")
        else:
            gaps.append("GoPlus · доли держателей неизвестны")
        details.append("GoPlus · проверены доступные признаки контракта")
    except Exception as exc:
        gaps.append("GoPlus · " + failure(exc))
    try:
        pairs = read_json(sources["DEX Screener"])
        if not isinstance(pairs, list):
            raise ValueError("Unexpected pairs")
        liquidities = []
        for pair in pairs:
            tokens = {pair.get(k, {}).get("address", "").lower() for k in ("baseToken", "quoteToken")}
            if pair.get("chainId") == network and address in tokens:
                usd = number((pair.get("liquidity") or {}).get("usd"))
                if usd is not None and usd >= 0:
                    liquidities.append(usd)
        if liquidities:
            usd = max(liquidities)
            details.append(f"DEX Screener · крупнейший наблюдаемый пул \u0024{usd:,.0f}")
            if usd < 25000:
                alerts.append("ликвидность крупнейшего пула ниже $25 000")
        else:
            gaps.append("DEX Screener · нет ликвидности с подтверждённой парой сеть/адрес")
    except Exception as exc:
        gaps.append("DEX Screener · " + failure(exc))
    try:
        price = number(read_json(sources["DeFiLlama"])["coins"][network + ":" + address]["price"])
        if price is None or price <= 0:
            raise ValueError("Missing quote")
        details.append(f"DeFiLlama · справочная цена \u0024{price:g}")
    except Exception as exc:
        gaps.append("DeFiLlama · " + failure(exc))
    try:
        reported, note = complaints(chain, address, month_cache)
        if reported is True:
            alerts.append("Chainabuse · есть публичная жалоба")
        elif reported is False:
            details.append("Chainabuse · жалоб в выборке нет")
        else:
            gaps.append("Chainabuse · " + note)
    except Exception as exc:
        gaps.append("Chainabuse · " + failure(exc))
    if not reference:
        gaps.append("Нет официальной ссылки с адресом")
    status = HIGH if flags else REVIEW if alerts else UNKNOWN if gaps else LOW
    return dict(chain_id=chain, address=address, status=status, reasons=flags + alerts + gaps + details, sources=sources)
