#!/usr/bin/env python3
"""Crypto Intelligence v2. Read-only scanner, with optional existing notifications."""
import datetime as dt
import json
import os
import pathlib
import tempfile
import urllib.parse
import urllib.request

from intelligence import airdrop_hunter, market_analyst, scam_detector, social_monitor
from intelligence.net import blocked, canonical, failure, fetch
from intelligence.reports import build_html, build_markdown
from intelligence.social_monitor import SOURCES, parse_feed

ROOT = pathlib.Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DATA = ROOT / "data"
STATE_PATH = DATA / "seen.json"


def get_market_data(selected=()):
    return market_analyst.prices(fetch, selected)


def read_state():
    if not STATE_PATH.exists():
        return {"seen": []}
    obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("seen"), list):
        raise ValueError("Invalid history; refusing to reset it")
    return obj


def save(path, content):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                     delete=False) as handle:
        temp = pathlib.Path(handle.name)
        try:
            handle.write(content)
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def send_telegram(payload: dict) -> str:
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        return 'Не настроен (необязательно)'
    msg = ('Crypto Auto Agents: ' + payload['time_utc'] + ' UTC\n' +
           f"Новых публикаций: {len(payload['new'])}; реальный заработок: $0\n")
    for c in payload['market']:
        msg += f"{c['symbol']}: ${c['usd']} ({c['change24h']}% за 24ч)\n"
    for x in payload['top']:
        msg += '\n' + x['title'][:90] + '\n' + x['url']
    data = urllib.parse.urlencode({'chat_id': chat, 'text': msg[:3950], 'disable_web_page_preview': 'true'}).encode()
    url = 'https://api.telegram.org/bot' + token + '/sendMessage'
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as rsp:
            rsp.read(300)
        return 'Отправлено'
    except Exception as exc:
        return f'Ошибка Telegram ({type(exc).__name__}), проверь secrets'



def run():
    REPORTS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    state = read_state()
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    month = now.strftime("%Y-%m")
    seen = set(state["seen"])
    cache = state.get("chainabuse", {}).get(month, {})
    blocked.clear()
    health = {}
    try:
        config = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
        if not isinstance(config.get("contracts"), list) or not isinstance(config.get("market_ids"), list):
            raise ValueError("Invalid watchlist")
    except (OSError, ValueError, AttributeError) as exc:
        health["watchlist.json"] = failure(exc)
        config = {"market_ids": [], "contracts": []}
    market = []
    try:
        market = get_market_data(config["market_ids"])
        expected = set(market_analyst.CORE) | {i for i in config["market_ids"] if isinstance(i, str)}
        missing = expected - {x["id"] for x in market if x["usd"] is not None}
        health["CoinGecko"] = "Нет данных · " + ", ".join(sorted(missing)) if missing else "OK"
    except Exception as exc:
        health["CoinGecko"] = failure(exc)
    news = social_monitor.collect(health)
    if not news and not market and not any(health.get(s, "").startswith("OK") for s in SOURCES):
        for name, status in health.items():
            print(f"{name}: {status}")
        raise RuntimeError("Market and publication feeds unavailable; retaining previous reports and history")
    market_analyst.compare(market, news, today)
    items = airdrop_hunter.group(news, today)
    targets = {}
    for contract in config["contracts"]:
        try:
            pair = (contract["chain_id"], contract["address"].lower())
            if not scam_detector.valid_pair(*pair):
                raise ValueError("Invalid contract")
            targets[pair] = canonical(contract.get("reference", ""))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            health["watchlist.json · контракт"] = failure(exc)
    for item in items:
        if item["token"]:
            targets.setdefault(item["token"], item["url"])
    assessments = {}
    for pair, reference in list(targets.items())[:4]:
        assessments[pair] = scam_detector.inspect(*pair, reference, cache)
    if len(targets) > len(assessments):
        health["Контракты"] = f"Лимит за запуск · проверено {len(assessments)} из {len(targets)}"
    for item in items:
        pair = item["token"]
        item["risk"] = assessments.get(pair) or {
            "status": scam_detector.UNKNOWN,
            "reasons": ["Лимит проверок контрактов за запуск" if pair else "В первоисточнике не установлена пара сеть/адрес",
                        "Условия акции и ссылки для получения награды требуют ручной проверки"],
            "sources": item["sources"],
        }
        item["published"] = str(airdrop_hunter.as_date(item["published"]) or "")
    latest = airdrop_hunter.rank(items)
    new = [item for item in latest if seen.isdisjoint([item["id"], *item["aliases"]])]
    top = [item for item in new if item["eligible"]][:5]
    # Refresh retained IDs in observation order, including v1 hashes.
    history = dict.fromkeys(state["seen"])
    for item in latest:
        for key in [*item.pop("aliases"), item["id"]]:
            history.pop(key, None)
            history[key] = None
    payload = dict(time_utc=now.strftime("%Y-%m-%d %H:%M"), market=market, new=new, latest=latest, top=top,
                   health=health, contracts=list(assessments.values()), real_earnings_usd=0)
    health["Telegram · уведомления"] = send_telegram(payload)
    outputs = {"LATEST.md": build_markdown(payload), "LATEST.html": build_html(payload),
               "latest.json": json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)}
    for name, content in outputs.items():
        save(REPORTS / name, content)
    save(STATE_PATH, json.dumps({"seen": list(history)[-6000:], "chainabuse": {month: cache}}, indent=2))
    print(f"{payload['time_utc']} UTC · market {len(market)}, candidates {len(items)}, new {len(new)}")
    return payload


if __name__ == "__main__":
    run()
