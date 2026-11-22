"""Read-only checks of public program rules. A social mention is only a lead."""
import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.error import HTTPError

from .net import canonical, digest, failure, fetch

CATALOG = Path(__file__).with_name("reward_programs.json")
CATEGORIES = {
    "learn": r"learn\s*(?:and|&)\s*earn|награды? за обучен",
    "testnet": r"incentivized testnet|testnet rewards?|наград\w* за тестнет",
    "quest": r"quests?|квест\w*",
    "grant": r"grants?|грант\w*",
    "bounty": r"bug bount(?:y|ies)|баунти",
    "airdrop": r"airdrops?|а[ий]рдроп\w*|эйрдроп\w*",
}
REWARD_TYPES = {"guaranteed": "Гарантирована при выполнении опубликованных условий",
                "conditional": "Потенциальная, зависит от условий или отбора",
                "speculative": "Спекулятивная, реальная выплата не подтверждена",
                "none": "Переводимая награда отсутствует"}


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = set()

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript"}:
            self.hidden.add(tag)

    def handle_endtag(self, tag):
        self.hidden.discard(tag)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def mentions(content, programs=None):
    programs = catalog() if programs is None else programs
    return {p["id"] for p in programs
            if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", content, re.I)
                   for alias in p["aliases"])}


def inspect(program, mentioned, today, reader=None):
    item = {**program, "telegram_mention": program["id"] in mentioned,
            "status": "found", "checks": {}, "blockers": []}
    for url, fragments in program["evidence"].items():
        try:
            if not canonical(url) or not fragments:
                raise ValueError("Official evidence required")
            page = PageText()
            raw = reader(url) if reader else fetch(url, exact_url=True)
            page.feed(raw.decode("utf-8", "replace"))
            visible = " ".join(" ".join(page.parts).casefold().split())
            matched = all(" ".join(fragment.casefold().split()) in visible for fragment in fragments)
            item["checks"][url] = "OK" if matched else "Правила изменились или страница недоступна для чтения"
        except (HTTPError, OSError, RuntimeError, ValueError) as exc:
            item["checks"][url] = failure(exc)
    if not item["checks"] or any(value != "OK" for value in item["checks"].values()):
        item["blockers"].append("Текущие правила не подтверждены")
        return item
    item["status"] = "checked"
    deadline = program["deadline"]
    if deadline != "rolling" and (not deadline or dt.date.fromisoformat(deadline) < today):
        item["blockers"].append("Срок истёк или требует проверки")
    for field, expected, message in (
        ("germany", "eligible", "Допуск для Германии требует проверки"),
        ("deposit", "none", "Отсутствие обязательного депозита не подтверждено"),
        ("fees", "none", "Возможны расходы до получения награды"),
        ("access", "public", "Доступ к конкретному заданию нужно проверить у организатора"),
    ):
        if program[field] != expected:
            item["blockers"].append(message)
    if program["kyc"] not in {"none", "required"}:
        item["blockers"].append("Требования KYC неизвестны")
    if program["reward"] not in {"guaranteed", "conditional"} or not program["payout"]:
        item["blockers"].append("Проверяемый способ получения реальной награды отсутствует")
    if not program["steps"]:
        item["blockers"].append("Шаги участия не подтверждены")
    if not item["blockers"]:
        item["status"] = "available"
    return item


def discover(news):
    """New official feed entries are leads until campaign-specific terms are reviewed."""
    from .airdrop_hunter import extract

    leads = {}
    programs = catalog()
    catalog_urls = {canonical(p["url"]) for p in programs}
    for post in news:
        url = canonical(post["url"])
        if not post["official"] or not url or url in catalog_urls:
            continue
        title = post["title"]
        category = next((name for name, pattern in CATEGORIES.items()
                         if re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", title, re.I)), None)
        if not category or mentions(title, programs):
            continue
        item = dict(id=digest(url), title=title, url=url, category=category, status="found",
                    germany="unknown", kyc="unknown", fees="unknown", deposit="unknown", deadline=None,
                    reward="speculative" if category in {"airdrop", "testnet"} else "conditional",
                    payout="", steps=[], risks=["Требуется проверка условий первоисточника"],
                    blockers=["Депозит, допуск, комиссия, срок и выплата ещё не проверены"],
                    telegram_mention="Telegram · локальный сканер" in post["mentions"],
                    published_terms=extract(post), checks={})
        leads[url] = item
        if len(leads) >= 40:
            break
    return list(leads.values())


def events(items, today):
    result = []
    for item in items:
        if item["status"] != "available":
            continue
        text = (f"{item['title']} · {REWARD_TYPES[item['reward']]}\n"
                f"{item['url']}\n"
                f"Германия: допуск указан. Депозит и авансовые комиссии: отсутствуют. "
                f"KYC: {'требуется' if item['kyc'] == 'required' else 'не указан как обязательный'}.\n"
                + "\n".join(f"{i}. {step}" for i, step in enumerate(item["steps"], 1))
                + f"\nПолучение: {item['payout']}\nРиски: {'; '.join(item['risks'])}")
        result.append(("program:" + item["id"], text))
        end = item["deadline"]
        if end != "rolling" and 0 <= (dt.date.fromisoformat(end) - today).days <= 3:
            result.append((f"deadline:{item['id']}:{end}",
                           f"Срок до {end} · {item['title']}\n{item['url']}"))
    return result


def markdown(report):
    from .reports import md, md_link

    labels = {"learn": "Learn & Earn", "testnet": "Тестовая сеть", "quest": "Квест",
              "grant": "Грант", "bounty": "Bug bounty", "airdrop": "Аирдроп"}
    stages = {"found": "найдено", "checked": "проверено", "available": "доступно",
              "submitted": "заявка подана", "confirmed": "награда подтверждена", "received": "реально получено"}
    terms = {"unknown": "требует проверки", "none": "отсутствует", "required": "требуется",
             "eligible": "допуск указан", "withdrawal": "при выводе"}
    progress = report["progress"]
    lines = ["# Crypto Cash Hunter v4", "", f"Проверка {report['checked_utc']}",
             f"Фактически получено {progress['real_earnings_usd']} USD.",
             " · ".join(f"{stages[k]} {v}" for k, v in progress["stages"].items())]
    for item in sorted([*report["programs"], *report["leads"]],
                       key=lambda p: (list(stages).index(p["status"]) * -1, p["id"])):
        lines += ["", "## " + md_link(item["title"], item["url"]), "",
                  f"{labels[item['category']]} · {stages[item['status']]} · ID `{item['id']}`", "",
                  f"- Германия — {terms[item['germany']]}",
                  f"- KYC — {terms[item['kyc']]}",
                  f"- Комиссия — {terms[item['fees']]}",
                  f"- Депозит — {terms[item['deposit']]}",
                  "- Срок — " + ("действует постоянно" if item["deadline"] == "rolling" else item["deadline"] or "требует проверки"),
                  "- Награда — " + REWARD_TYPES[item["reward"]],
                  "- Получение — " + md(item["payout"] or "требует проверки"),
                  "- Риски — " + md(". ".join(item["risks"])),
                  "- Упоминание в папке «Крипта» — " + ("есть" if item["telegram_mention"] else "отсутствует")]
        lines += ["- Проверка — " + md(reason) for reason in item["blockers"]]
        for url, outcome in item["checks"].items():
            lines.append("- " + md_link("Источник условий", url) + " · " + md(outcome))
        if item["steps"]:
            lines += ["", *(f"{n}. {md(step)}" for n, step in enumerate(item["steps"], 1))]
    return "\n".join(lines) + "\n"
