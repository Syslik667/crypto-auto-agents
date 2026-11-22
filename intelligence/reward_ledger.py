"""Local progress and explicitly attested net receipts."""
import argparse
import contextlib
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3

STAGES = ("found", "checked", "available", "submitted", "confirmed", "received")


def setup(db):
    db.execute("CREATE TABLE IF NOT EXISTS progress (id TEXT PRIMARY KEY, stage INTEGER NOT NULL,"
               " net_cents INTEGER NOT NULL DEFAULT 0)")


def observe(db, items):
    setup(db)
    with db:
        db.executemany("INSERT INTO progress(id,stage) VALUES (?,?) ON CONFLICT(id) "
                       "DO UPDATE SET stage=max(progress.stage,excluded.stage)",
                       [(item["id"], STAGES.index(item["status"])) for item in items])


def record(db, item, stage, net_usd=None):
    if stage not in STAGES[3:] or (net_usd is not None) != (stage == "received"):
        raise ValueError("Explicit receipt amount required only for received")
    cents = 0
    if net_usd is not None:
        if not net_usd.is_finite() or not 0 < net_usd <= 1_000_000_000 or net_usd.as_tuple().exponent < -2:
            raise ValueError("Enter actual positive net USD to at most two decimal places")
        cents = int(net_usd * 100)
    setup(db)
    rank = STAGES.index(stage)
    with db:
        minimum = STAGES.index("confirmed" if stage == "received" else "checked")
        updated = db.execute("UPDATE progress SET stage=?, net_cents=? WHERE id=? AND stage>=? AND stage<?",
                             (rank, cents, item, minimum, rank))
        if not updated.rowcount:
            raise ValueError("Unknown candidate, missing previous confirmation or already recorded")


def summary(db):
    setup(db)
    result = dict.fromkeys(STAGES, 0)
    cents = 0
    for rank, count, net_cents in db.execute("SELECT stage, count(*), sum(net_cents) FROM progress GROUP BY stage"):
        result[STAGES[rank]] = count
        cents += net_cents
    return {"stages": result, "real_earnings_usd": str((Decimal(cents) / 100).quantize(Decimal(".01")))}


def draft(directory, item_id):
    from agent import save

    report = json.loads((directory / "reward-report.json").read_text(encoding="utf-8"))
    item = next((p for p in report["programs"] if p["id"] == item_id), None)
    if not item or not re.fullmatch(r"[a-z0-9-]+", item_id) or item["category"] not in {"grant", "bounty"}:
        raise ValueError("Choose a grant or bounty ID from the local report")
    fields = ["Цель проекта", "Методология", "Этапы и сроки", "Бюджет", "Публичный результат"]
    if item["category"] == "bounty":
        fields = ["Программа и допустимый охват", "Заголовок личного отчёта", "Применимые правила раскрытия"]
    lines = [item["title"], item["url"], "", *item["steps"], "", *item["blockers"],
             "", "Черновик для ручного заполнения", *[f"• {field} — " for field in fields]]
    target = directory / f"draft-{item_id}.txt"
    if target.exists():
        raise ValueError("Draft already exists")
    save(target, "\n".join(lines) + "\n")
    return target.name


def main():
    from local_telegram.scanner import private_directory

    parser = argparse.ArgumentParser(description="Inspect local progress or record something you already did.")
    parser.add_argument("--id")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", choices=STAGES[3:])
    mode.add_argument("--draft", metavar="ID", help="Prepare a local application template")
    parser.add_argument("--net-usd", type=Decimal)
    parser.add_argument("--confirm", action="store_true", help="Attest to the local record")
    args = parser.parse_args()
    if args.record and not (args.id and args.confirm):
        parser.error("--record requires --id and --confirm")
    if not args.record and (args.net_usd is not None or args.id or args.confirm):
        parser.error("recording options require --record")
    directory = private_directory()
    try:
        if args.draft:
            print(draft(directory, args.draft))
            return
        with contextlib.closing(sqlite3.connect(directory / "rewards.sqlite3")) as db:
            if args.record:
                record(db, args.id, args.record, args.net_usd)
            print(json.dumps(summary(db), indent=2))
    except FileNotFoundError:
        parser.error("Run the local monitor before preparing a draft")
    except (InvalidOperation, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
