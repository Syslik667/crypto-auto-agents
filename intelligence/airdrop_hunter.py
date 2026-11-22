"""Evidence from a publisher is a claim. Unknown terms stay unknown."""
import datetime as dt
import re
from email.utils import parsedate_to_datetime

from .net import digest
from .social_monitor import TRIGGERS

UNKNOWN = "не указано"
CHECKS = {
    "Участие": [(r"\b(?:complete|take|pass) (?:the |a )?quiz\b", "пройти викторину"),
                (r"\b(?:join|register for) (?:the |a )?waitlist\b", "запись в лист ожидания"),
                (r"\btrading (?:volume|competition)\b", "торговая активность"),
                (r"\b(?:stake|staking)\b", "упоминается стейкинг")],
    "Депозит": [(r"\b(?:no deposit(?: required)?|without (?:a )?deposit|deposit is not required)\b", "заявлено без депозита"),
                (r"(?<!no )\b(?:minimum deposit|deposit (?:at least|(?:is )?required|\$?\d+)|must deposit)\b", "требуется")],
    "Комиссии": [(r"\b(?:gas[- ]free|no (?:gas|transaction) fees?)\b", "заявлено без комиссии"),
                 (r"(?<!not )(?<!no )\b(?:pay (?:the )?gas|gas fees? (?:apply|required)|transaction fees? apply)\b", "требуются")],
    "KYC": [(r"\b(?:no kyc|without kyc)\b", "заявлено без KYC"),
            (r"(?<!no )\b(?:kyc (?:is )?(?:mandatory|required)|must (?:complete|pass) kyc)\b", "требуется")],
    "Германия": [(r"\b(?:not available in|excludes?|excluding|unavailable in) Germany\b", "исключена"),
                 (r"(?<!not )(?<!un)\bavailable in Germany\b|\bGerman residents are eligible\b", "заявлено участие")],
    "Вывод": [(r"\b(?:non[- ]transferable|cannot be withdrawn|withdrawals disabled)\b", "ограничен"),
              (r"\b(?:rewards? (?:can be withdrawn|are withdrawable))\b", "заявлен, фактически не проверен")],
}
NONCASH = re.compile(r"\b(xp|points?|waitlist)\b", re.I)
ADDRESS = re.compile(r"\b(ethereum|base|arbitrum)(?: mainnet)?\s+(?:token |contract )*(?:address|contract)\s*[:=]\s*(0x[a-f0-9]{40})\b", re.I)
CHAINS = {"ethereum": "1", "base": "8453", "arbitrum": "42161"}


def as_date(value):
    try:
        return dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        try:
            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError, OverflowError):
            return None


def extract(item):
    content = item["title"] + ". " + item["summary"]
    terms = {}
    for field, patterns in CHECKS.items():
        matches = {label for pattern, label in patterns if re.search(pattern, content, re.I)}
        terms[field] = " / ".join(sorted(matches)) or UNKNOWN
    until = re.search(r"\b(?:ends?(?: on)?|deadline|until|closes?(?: on)?)\s*[:–-]?\s*"
                      r"(\d{4}-\d\d-\d\d|[A-Za-z]{3,9} \d{1,2},? \d{4}|\d{1,2} [A-Za-z]{3,9} \d{4})\b", content, re.I)
    deadline = None
    if until:
        for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
            try:
                deadline = dt.datetime.strptime(until[1].replace(",", ""), fmt).date()
                break
            except ValueError:
                continue
    terms["Окончание"] = deadline.isoformat() if deadline else UNKNOWN
    noncash = sorted({"points" if s.lower() == "point" else s.lower() for s in NONCASH.findall(content)})
    terms["Награда"] = " / ".join(noncash) + " · денежная сумма не установлена" if noncash else "вид и стоимость не подтверждены"
    return terms


def group(news, today):
    candidates = [x for x in news if TRIGGERS.search(x["title"] + " " + x["summary"])]
    official = {x["url"]: x for x in news if x["official"]}
    groups = {}
    for x in candidates:
        targets = x["refs"] & official.keys() if not x["official"] else set()
        # Generic links and unrelated news are not proof of campaign identity.
        targets = {u for u in targets if TRIGGERS.search(official[u]["title"])}
        url = next(iter(targets)) if len(targets) == 1 else x["url"]
        groups.setdefault(url, []).append(x)
    result = []
    for url, posts in groups.items():
        primary = official.get(url, posts[0])
        evidence = {p["url"]: p for p in [*posts, primary]}
        terms = extract(primary)
        end = as_date(terms["Окончание"])
        content = primary["title"] + " " + primary["summary"]
        pairs = {(CHAINS[c.lower()], a.lower()) for c, a in ADDRESS.findall(content)}
        token = next(iter(pairs)) if primary["official"] and len(pairs) == 1 else None
        published = as_date(primary["published"])
        exclusions = []
        if not published or not 0 <= (today - published).days <= 14:
            exclusions.append("дата публикации вне последних 14 дней или неизвестна")
        if end and end < today:
            exclusions.append("срок завершён")
        if re.search(r"\b(?:casino|sportsbook|futures|perpetuals?|perps|prop trading|trading competition)\b", content, re.I):
            exclusions.append("упоминаются ставки, деривативы или платная торговая активность")
        result.append(dict(id=digest(url), aliases=sorted({k for p in evidence.values() for k in (p["id"], digest(p["url"]))}),
                           title=primary["title"], source=primary["source"], url=url, official=primary["official"],
                           published=primary["published"], terms=terms, token=token,
                           sources={p["source"]: p["url"] for p in evidence.values()},
                           mentions=sorted(primary["mentions"]), status=primary["status"],
                           exclusions=exclusions, expired=bool(end and end < today)))
    return result


def rank(items):
    for item in items:
        terms = item["terms"]
        known = sum(terms[k] != UNKNOWN for k in CHECKS)
        item["summary"] = ". ".join(f"{k} — {v}" for k, v in terms.items() if v != UNKNOWN)
        reasons = ["официальная публикация" if item["official"] else "публикация каталога, первоисточник не подтверждён"]
        if known:
            reasons.append(f"указано условий {known} из {len(CHECKS)}")
        if item["mentions"]:
            reasons.append("есть совпадающая ссылка в соцканале")
        if terms["Депозит"] == "заявлено без депозита":
            reasons.append("источник заявляет участие без депозита")
        item["selection"] = ", ".join(reasons)
        for field, labels in (("Германия", ("исключена",)), ("Депозит", ("требуется",)),
                              ("Комиссии", ("требуются",)), ("Вывод", ("ограничен",)),
                              ("Участие", ("торговая активность", "стейкинг"))):
            if any(label in terms[field] for label in labels):
                item["exclusions"].append(field + " — " + terms[field])
        if item["risk"]["status"] == "Высокий риск":
            item["exclusions"].append("высокий риск контракта")
        item["exclusions"] = list(dict.fromkeys(item["exclusions"]))
        item["eligible"] = not item["exclusions"]
        item["score"] = (10 * int(item["official"]) + known + int(bool(item["mentions"]))
                         + 2 * int(terms["Депозит"] == "заявлено без депозита"))
    return sorted(items, key=lambda x: (-x["score"], -(as_date(x["published"]) or dt.date.min).toordinal(), x["url"]))
