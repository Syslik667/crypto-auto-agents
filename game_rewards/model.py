"""Normalize manually transcribed offers and rank them with explicit assumptions."""
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import re
import unicodedata

from .sources import PLATFORMS, offer_url

CURRENCIES = {"EUR": 2, "USD": 2, "GBP": 2, "BTC": 8, "LTC": 8, "SOL": 9, "USDC": 6, "POINTS": 3}
DEVICES = {"android", "ios", "pc", "any", "unknown"}
OPTIONS = {"yes", "no", "unknown"}
GAMBLING = re.compile(r"\b(?:casino|sportsbook|slot machines?|wager|sports betting|казино|букмекер\w*|ставк\w* на спорт)\b", re.I)


def text(value, size, label, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label}: требуется текст.")
    cleaned = " ".join(value.split())
    if len(cleaned) > size or (required and not cleaned):
        raise ValueError(f"{label}: допустимо до {size} символов.")
    return cleaned


def numeric(value, label, places=2, maximum=1_000_000):
    if value is None or value == "":
        return None
    value = str(value).strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?", value):
        raise ValueError(f"{label}: введите неотрицательное число.")
    try:
        amount = Decimal(value)
        rounded = amount.quantize(Decimal(1).scaleb(-places))
        if amount > maximum or amount != rounded:
            raise ValueError(f"{label}: превышен диапазон или точность.")
    except InvalidOperation:
        raise ValueError(f"{label}: неверное число.") from None
    return format(rounded, "f")


def amount_label(amount, currency):
    places = CURRENCIES[currency]
    value = f"{amount:.{places}f}"
    return value if places == 2 else value.rstrip("0").rstrip(".") or "0"


def date(value):
    if not value:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Укажите дату в формате ГГГГ-ММ-ДД.")
    return dt.date.fromisoformat(value)


def choice(value, allowed, label):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label}: неизвестное значение.")
    return value


def identity(value):
    return unicodedata.normalize("NFKC", value).casefold()


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError("Карточка должна быть объектом.")
    platform = choice(raw.get("platform"), PLATFORMS, "Площадка")
    title = text(raw.get("title"), 120, "Название игры", required=True)
    device = choice(raw.get("device", "unknown"), DEVICES, "Устройство")
    currency = choice(raw.get("currency"), CURRENCIES, "Валюта")
    ref = text(raw.get("external_id"), 60, "ID предложения")
    if ref and not re.fullmatch(r"[A-Za-z0-9_.-]+", ref):
        raise ValueError("ID предложения: допустимы буквы, цифры, точка, дефис и подчёркивание.")
    url = offer_url(platform, text(raw.get("url"), 500, "Ссылка"))
    stages = raw.get("milestones")
    if not isinstance(stages, list) or not 1 <= len(stages) <= 30 or any(not isinstance(row, dict) for row in stages):
        raise ValueError("Добавьте от 1 до 30 этапов.")
    places = CURRENCIES[currency]
    milestones = [{"label": text(row.get("label"), 160, "Этап", required=True),
                   "amount": numeric(row.get("amount"), "Награда этапа", places)} for row in stages]
    if len({identity(s["label"]) for s in milestones}) != len(milestones):
        raise ValueError("Названия этапов должны различаться.")
    lower = numeric(raw.get("minutes_min"), "Минимум времени", 0, 100_000)
    upper = numeric(raw.get("minutes_max"), "Максимум времени", 0, 100_000)
    if (lower or upper) and (not lower or not upper or not 0 < int(lower) <= int(upper)):
        raise ValueError("Введите обе оценки времени и проверьте порядок.")
    deadline = date(raw.get("deadline"))
    days = numeric(raw.get("days"), "Дней от установки", 0, 365)
    if days == "0":
        raise ValueError("Дней от установки: укажите положительный срок.")
    return {
        "platform": platform, "title": title, "url": url, "external_id": ref, "device": device,
        "key": hashlib.sha256(f"{platform}\n{identity(title)}\n{device}".encode()).hexdigest(),
        "germany": choice(raw.get("germany", "unknown"), OPTIONS, "Германия"),
        "deposit": choice(raw.get("deposit", "unknown"), OPTIONS, "Депозит"),
        "purchase": choice(raw.get("purchase", "unknown"), OPTIONS, "Покупки"),
        "kyc": choice(raw.get("kyc", "unknown"), OPTIONS, "KYC"),
        "prior_install": choice(raw.get("prior_install", "unknown"), OPTIONS, "Первая установка"),
        "fee_mode": choice(raw.get("fee_mode", "unknown"), {"none", "deducted", "upfront", "unknown"}, "Комиссия"),
        "fee": numeric(raw.get("fee"), "Комиссия", places),
        "minimum": numeric(raw.get("minimum"), "Порог вывода", places),
        "cost": numeric(raw.get("cost"), "Обязательные расходы", places),
        "payout_method": text(raw.get("payout_method"), 120, "Способ вывода"),
        "currency": currency,
        "rate_eur": numeric(raw.get("rate_eur"), "Курс в EUR", 10),
        "minutes_min": lower, "minutes_max": upper,
        "probability": numeric(raw.get("probability"), "Вероятность", 0, 100),
        "deadline": deadline.isoformat() if deadline else None, "days": int(days) if days else None,
        "reviewed": raw.get("reviewed") is True, "notes": text(raw.get("notes"), 900, "Условия и заметки"),
        "milestones": milestones,
    }


def analyze(offer, today, started=None):
    end = date(offer["deadline"])
    relative = date(started) + dt.timedelta(days=offer["days"]) if started and offer["days"] else None
    candidates = [value for value in (end, relative) if value]
    deadline = min(candidates) if candidates else None
    currency = offer["currency"]
    fee = Decimal(0) if offer["fee_mode"] == "none" else (
        Decimal(offer["fee"]) if offer["fee_mode"] in {"deducted", "upfront"} and offer["fee"] is not None else None)
    cost = Decimal(offer["cost"]) if offer["cost"] is not None else (
        Decimal(0) if offer["deposit"] == offer["purchase"] == "no" else None)
    zero_cost = (
        offer["deposit"] == offer["purchase"] == "no" and offer["fee_mode"] in {"none", "deducted"}
        and fee is not None and cost == 0
    )
    gross = (sum((Decimal(s["amount"]) for s in offer["milestones"]), Decimal(0))
             if all(s["amount"] is not None for s in offer["milestones"]) else None)
    title_and_steps = " ".join([offer["title"], *(s["label"] for s in offer["milestones"])])
    reasons = []
    for condition, reason in (
        (offer["germany"] != "yes", "Доступность в Германии не подтверждена"),
        (offer["device"] == "unknown", "Не указано совместимое устройство"),
        (not zero_cost, "Есть авансовые расходы или не подтверждены депозит, покупки и комиссия"),
        (offer["kyc"] == "unknown", "Условия KYC неизвестны"),
        (not (deadline or offer["days"]), "Срок выполнения неизвестен"),
        (deadline is not None and deadline < today, "Срок завершился"),
        (not offer["payout_method"] or offer["minimum"] is None, "Неизвестен способ или минимум вывода"),
        (gross is not None and offer["minimum"] is not None and gross < Decimal(offer["minimum"]),
         "Награды за эту игру недостаточно для опубликованного минимума вывода"),
        (gross is None or gross <= 0 or currency == "POINTS", "Сумма переводимой награды не подтверждена"),
        (fee is not None and gross is not None and fee >= gross, "Комиссия поглощает награду"),
        (not offer["reviewed"], "Требуется сверка текущего предложения пользователем"),
        (offer["platform"] == "freecash" and offer["prior_install"] == "yes", "Игра уже устанавливалась"),
        (GAMBLING.search(title_and_steps), "Возможны азартные игры или ставки"),
    ):
        if condition:
            reasons.append(reason)

    fx = Decimal(1) if currency == "EUR" else Decimal(offer["rate_eur"] or "0")
    probability = Decimal(offer["probability"]) / 100 if offer["probability"] is not None else None
    rates = None
    if (currency != "POINTS" and gross is not None and fee is not None and cost is not None and fx > 0
            and offer["deposit"] != "unknown" and offer["purchase"] != "unknown"
            and (offer["deposit"] == offer["purchase"] == "no" or cost > 0)
            and probability is not None and offer["minutes_min"]):
        upfront = fee if offer["fee_mode"] == "upfront" else Decimal(0)
        expected = ((gross - (fee - upfront)) * probability - cost - upfront) * fx
        estimates = sorted(expected * 60 / Decimal(offer[minutes]) for minutes in ("minutes_min", "minutes_max"))
        rates = [f"{value:.2f}" for value in estimates]
    risk = min(3, len(reasons) + int(offer["kyc"] == "yes") + int(offer["prior_install"] == "unknown"))
    return {"gross": amount_label(gross, currency) if gross is not None else None,
            "rates": rates, "risk": risk, "zero_cost": zero_cost, "eligible": not reasons,
            "reasons": reasons, "deadline": deadline.isoformat() if deadline else None}


def rank(item):
    evaluation = item["analysis"]
    lower = Decimal(evaluation["rates"][0]) if evaluation["rates"] else Decimal("-Infinity")
    penalty = Decimal(1) + Decimal(".15") * evaluation["risk"]
    adjusted = lower / penalty if lower >= 0 else lower * penalty
    return (not evaluation["eligible"], -adjusted, -int(item["probability"] or 0),
            evaluation["risk"], item["title"].casefold())
