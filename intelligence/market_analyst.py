import datetime as dt
import json
import re
from urllib.parse import urlencode

from .airdrop_hunter import as_date
from .net import number

CORE = ("bitcoin", "ethereum", "solana")
MARKETS = "https://api.coingecko.com/api/v3/coins/markets"


def prices(fetch, selected=()):
    valid = (i for i in selected if isinstance(i, str) and re.fullmatch(r"[a-z0-9-]{1,80}", i))
    ids = list(dict.fromkeys([*CORE, *valid]))[:25]
    query = urlencode(dict(vs_currency="usd", ids=",".join(ids), per_page=len(ids), page=1,
                           order="market_cap_desc", sparkline="false", price_change_percentage="24h"))
    rows = json.loads(fetch(MARKETS + "?" + query))
    if not isinstance(rows, list):
        raise ValueError("Unexpected market response")
    result = []
    for row in rows:
        if isinstance(row, dict) and row.get("id") in ids:
            result.append(dict(id=row["id"], name=row["id"].replace("-", " ").title(),
                               symbol=re.sub(r"[^A-Z0-9-]", "", str(row.get("symbol", "")).upper())[:15],
                               usd=number(row.get("current_price")), change24h=number(row.get("price_change_percentage_24h")),
                               source="https://www.coingecko.com/en/coins/" + row["id"]))
    return result


def compare(market, news, today):
    for row in market:
        names = [row["name"], row["symbol"]]
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names if n) + r")\b", re.I)
        row["news"] = []
        for post in news:
            date = as_date(post["published"])
            if (post["official"] and date and dt.timedelta(0) <= today - date <= dt.timedelta(days=1)
                    and pattern.search(post["title"] + " " + post["summary"])):
                row["news"].append({"title": post["title"], "url": post["url"]})
            if len(row["news"]) == 3:
                break
