"""Local offer history, progress and explicitly attested receipts."""
import base64
import binascii
import datetime as dt
from decimal import Decimal
import json
import sqlite3

from . import model

STAGES = ("potential", "pending", "confirmed", "withdrawn")
MAX_IMAGE_BYTES = 3_000_000


def open_db(path):
    db = sqlite3.connect(path, timeout=8)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY, platform TEXT NOT NULL, match_key TEXT NOT NULL, ref TEXT NOT NULL,
            terms TEXT NOT NULL, started TEXT, picture_type TEXT, picture BLOB
        );
        CREATE UNIQUE INDEX IF NOT EXISTS offer_reference ON offers(platform,ref) WHERE ref <> '';
        CREATE INDEX IF NOT EXISTS offer_match ON offers(match_key);
        CREATE TABLE IF NOT EXISTS milestones (
            offer_id INTEGER REFERENCES offers(id), position INTEGER NOT NULL, label TEXT NOT NULL,
            quoted TEXT, state TEXT NOT NULL DEFAULT 'potential', reported TEXT, currency TEXT NOT NULL,
            PRIMARY KEY(offer_id,position)
        );
        CREATE TABLE IF NOT EXISTS review_log (
            id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id), checked TEXT NOT NULL, reasons TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS progress_log (
            id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id), position INTEGER NOT NULL,
            changed TEXT NOT NULL, state TEXT NOT NULL, amount TEXT, currency TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            recipient TEXT NOT NULL, event TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(recipient,event)
        );
    """)
    return db


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def validate_picture(item):
    if item is None:
        return None, None
    if not isinstance(item, dict):
        raise ValueError("Неверный формат скриншота.")
    mime, data = item.get("mime"), item.get("data")
    if not isinstance(data, str) or len(data) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("Скриншот должен быть не больше 3 МБ.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Не удалось прочитать скриншот.") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Скриншот должен быть не больше 3 МБ.")
    allowed = {"image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
               "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
               "image/webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"}
    if not isinstance(mime, str) or not allowed.get(mime):
        raise ValueError("Поддерживаются PNG, JPEG и WebP.")
    return mime, raw


def review(db, offer_id, offer, today, started):
    reasons = json.dumps(model.analyze(offer, today, started)["reasons"], ensure_ascii=False)
    previous = db.execute("SELECT reasons FROM review_log WHERE offer_id=? ORDER BY id DESC LIMIT 1",
                          (offer_id,)).fetchone()
    if previous is None or previous["reasons"] != reasons:
        db.execute("INSERT INTO review_log(offer_id,checked,reasons) VALUES (?,?,?)", (offer_id, utc_now(), reasons))


def save_offer(db, raw, today):
    data = model.normalize(raw)
    mime, picture = validate_picture(raw.get("screenshot"))
    saved = json.dumps(data, ensure_ascii=False)
    ref = data["external_id"]
    selected = raw.get("id")
    if selected is not None and (type(selected) is not int or not 1 <= selected < 10 ** 16):
        raise ValueError("Неверный номер карточки.")
    with db:
        db.execute("BEGIN IMMEDIATE")
        if selected is not None:
            original = db.execute("SELECT * FROM offers WHERE id=?", (selected,)).fetchone()
            if original is None:
                raise ValueError("Карточка не найдена.")
        row = db.execute("SELECT * FROM offers WHERE platform=? AND ref=? AND ref<>''",
                         (data["platform"], ref)).fetchone()
        if row is None:
            matches = db.execute("SELECT * FROM offers WHERE match_key=? AND (ref='' OR ?='') LIMIT 2",
                                 (data["key"], ref)).fetchall()
            if len(matches) > 1:
                raise ValueError("Есть несколько офферов этой игры. Укажите ID нужного предложения.")
            row = matches[0] if matches else None
        if selected is not None:
            if row is not None and row["id"] != selected:
                raise ValueError("Уже есть другая карточка с таким оффером.")
            row = original
        started = row["started"] if row else None
        if row is None:
            offer_id = db.execute(
                "INSERT INTO offers(platform,match_key,ref,terms,picture_type,picture) VALUES (?,?,?,?,?,?)",
                (data["platform"], data["key"], ref, saved, mime, picture),
            ).lastrowid
        else:
            offer_id = row["id"]
            previous = json.loads(row["terms"])
            if not ref and row["ref"]:
                data["external_id"] = row["ref"]
                saved = json.dumps(data, ensure_ascii=False)
            changed_stages = any(data[key] != previous[key] for key in ("milestones", "currency", "platform"))
            if changed_stages and db.execute(
                "SELECT 1 FROM milestones WHERE offer_id=? AND state<>'potential' LIMIT 1", (offer_id,)
            ).fetchone():
                raise ValueError("Суммы и этапы оффера с записанным прогрессом зафиксированы. Для новой акции укажите её ID.")
            if changed_stages:
                db.execute("DELETE FROM milestones WHERE offer_id=?", (offer_id,))
            db.execute("UPDATE offers SET terms=?,platform=?,match_key=?,ref=?,picture_type=coalesce(?,picture_type),"
                       "picture=coalesce(?,picture) WHERE id=?",
                       (saved, data["platform"], data["key"], data["external_id"], mime, picture, offer_id))
        db.executemany(
            "INSERT OR IGNORE INTO milestones(offer_id,position,label,quoted,currency) VALUES (?,?,?,?,?)",
            [(offer_id, position, stage["label"], stage["amount"], data["currency"])
             for position, stage in enumerate(data["milestones"])],
        )
        review(db, offer_id, data, today, started)
    return offer_id, row is None


def start(db, offer_id, started):
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT terms,started FROM offers WHERE id=?", (offer_id,)).fetchone()
        if row is None:
            raise ValueError("Предложение не найдено.")
        day = started.isoformat()
        if row["started"] and row["started"] != day:
            raise ValueError("Дата первого запуска уже записана.")
        db.execute("UPDATE offers SET started=? WHERE id=? AND started IS NULL", (day, offer_id))
        review(db, offer_id, json.loads(row["terms"]), dt.date.today(), day)


def advance(db, offer_id, position, state, attested, value=None, currency=None):
    if attested is not True or state not in STAGES[1:]:
        raise ValueError("Подтвердите действие, которое уже выполнено.")
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM milestones WHERE offer_id=? AND position=?", (offer_id, position)).fetchone()
        if row is None or STAGES[STAGES.index(state) - 1] != row["state"]:
            raise ValueError("Запишите следующий этап. Повтор уже сохранённого действия не требуется.")
        if state != "withdrawn":
            currency = row["currency"]
        currency = model.choice(currency, model.CURRENCIES, "Валюта факта")
        if state == "withdrawn" and currency == "POINTS":
            raise ValueError("Для фактического вывода укажите реально полученную валюту.")
        reported = row["quoted"] if state == "pending" else model.numeric(
            value, "Фактическая сумма", model.CURRENCIES[currency])
        if state != "pending" and (reported is None or Decimal(reported) <= 0):
            raise ValueError("Введите положительную сумму по факту зачисления или вывода.")
        args = (offer_id, position)
        db.execute("UPDATE milestones SET state=?,reported=?,currency=? WHERE offer_id=? AND position=?",
                   (state, reported, currency, *args))
        db.execute("INSERT INTO progress_log(offer_id,position,changed,state,amount,currency) VALUES (?,?,?,?,?,?)",
                   (*args, utc_now(), state, reported, currency))


def list_offers(db, today):
    stages = {}
    for row in db.execute("SELECT * FROM milestones ORDER BY position"):
        stages.setdefault(row["offer_id"], []).append(dict(row))
    results = []
    for row in db.execute("SELECT id,terms,started,picture_type FROM offers ORDER BY id DESC"):
        offer = json.loads(row["terms"])
        analysis = model.analyze(offer, today, row["started"])
        offer.update(id=row["id"], started=row["started"], screenshot=bool(row["picture_type"]),
                     analysis=analysis, milestones=stages.get(row["id"], []))
        results.append(offer)
    return sorted(results, key=model.rank)


def summary(db, today, offers=None):
    funds = {state: {} for state in STAGES}
    offers = list_offers(db, today) if offers is None else offers
    active = {offer["id"] for offer in offers if offer["analysis"]["eligible"]}
    for row in db.execute("SELECT offer_id,quoted,state,reported,currency FROM milestones"):
        state, currency = row["state"], row["currency"]
        if state == "potential" and row["offer_id"] not in active:
            continue
        raw = row["quoted"] if state == "potential" else row["reported"]
        if raw is not None:
            funds[state][currency] = funds[state].get(currency, Decimal(0)) + Decimal(raw)
    return {state: {c: model.amount_label(value, c) for c, value in totals.items()}
            for state, totals in funds.items()}


def journal(db):
    rows = db.execute("SELECT offer_id,checked,reasons FROM review_log ORDER BY id DESC LIMIT 100")
    return [dict(offer_id=row["offer_id"], checked=row["checked"], reasons=json.loads(row["reasons"])) for row in rows]


def screenshot(db, offer_id):
    row = db.execute("SELECT picture_type,picture FROM offers WHERE id=?", (offer_id,)).fetchone()
    return tuple(row) if row and row["picture_type"] else None
