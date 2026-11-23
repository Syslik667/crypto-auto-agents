"""One scheduled, local scan of the Telegram folder. No Git writes."""
import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3

import agent
from intelligence import reward_hunter, reward_ledger
from intelligence.net import blocked
from intelligence.telegram_client import TelegramClient, TelegramRejected
from . import scanner, vault


NO_REPEAT = "Без повторов"
PENDING = "Доставка не подтверждена · автоматический повтор заблокирован"


def deliver(db, bot, recipient, snapshot, events):
    # Reserve each item before the HTTP call. A timeout may mean Telegram already delivered it.
    db.execute("CREATE TABLE IF NOT EXISTS deliveries (recipient TEXT, event TEXT, status TEXT,"
               " PRIMARY KEY(recipient,event))")
    owner = hashlib.sha256(str(recipient).encode()).hexdigest()
    counts = snapshot["coverage"]
    batch = []
    with db:
        db.execute("BEGIN IMMEDIATE")
        def reserve(event):
            return db.execute("INSERT OR IGNORE INTO deliveries VALUES (?,?,'pending')",
                              (owner, event)).rowcount == 1
        lines = ["Crypto Cash Hunter · награды"]
        for event, message in events:
            if event.startswith("deadline:") and ("program:" + event.split(":")[1]) in batch:
                # The first notification already includes the deadline.
                if reserve(event):
                    batch.append(event)
                continue
            if len(lines) > 5:
                continue
            if len("\n\n".join([*lines, message]).encode("utf-16-le")) // 2 > 3800:
                continue
            if reserve(event):
                batch.append(event)
                lines.append(message)
        if reserve("initial-scan"):
            batch.append("initial-scan")
            if len(lines) == 1:
                lines = ["Crypto Cash Hunter v4 · папка «Крипта»", snapshot["generated_utc"],
                         f"Каналов проверено {counts['completed']}, сообщений {counts['messages_read']}, "
                         f"признаки риска в {counts['suspicious']}."]
        if not batch:
            pending = db.execute("SELECT 1 FROM deliveries WHERE recipient=? AND status='pending'", (owner,)).fetchone()
            return PENDING if pending else NO_REPEAT
    try:
        result = bot.call("sendMessage", {"chat_id": str(recipient), "text": "\n\n".join(lines),
                          "protect_content": "true", "link_preview_options": '{"is_disabled":true}'})
        if not isinstance(result, dict) or not result.get("message_id"):
            raise RuntimeError("Unconfirmed delivery")
    except TelegramRejected:
        with db:
            db.executemany("DELETE FROM deliveries WHERE recipient=? AND event=?", [(owner, k) for k in batch])
        return "Telegram отклонил отправку"
    except Exception:
        return PENDING
    with db:
        db.executemany("UPDATE deliveries SET status='sent' WHERE recipient=? AND event=?",
                       [(owner, k) for k in batch])
    return "Отправлено"


def notify(directory, snapshot, recipient, events):
    if not snapshot["coverage"]["completed"] and not events:
        return "Нет полностью проверенных каналов"
    token = vault.read_protected(directory / "bot.dpapi")
    if not token:
        return "Требуется локальная настройка бота"
    bot = TelegramClient(token)
    try:
        me = bot.call("getMe", {})
        if not me.get("is_bot") or me.get("username", "").lower() != "cryptohunterbotai":
            return "Неверный бот"
        with contextlib.closing(sqlite3.connect(directory / "deliveries.sqlite3")) as db:
            return deliver(db, bot, recipient, snapshot, events)
    except Exception as exc:
        return "Ошибка отправки · " + type(exc).__name__


def scan_and_report(directory):
    status = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "folder": "Крипта"}
    try:
        snapshot, recipient = asyncio.run(scanner.run(folder="Крипта", return_recipient=True))
        status.update(scanned_utc=snapshot["generated_utc"], coverage=snapshot["coverage"],
                      publisher_links=len(snapshot["links"]))
        previous_snapshot = os.environ.get("TELEGRAM_SCAN_PATH")
        os.environ["TELEGRAM_SCAN_PATH"] = str(directory / "public-links.json")
        leads = []
        try:
            payload = agent.run(output_root=directory / "monitor",
                                notifier=lambda _: "Используется локальный Reward Hunter", include_reward_leads=True)
            leads = payload["reward_leads"]
        except Exception as exc:
            status["v3_error"] = type(exc).__name__
        finally:
            if previous_snapshot is None:
                os.environ.pop("TELEGRAM_SCAN_PATH", None)
            else:
                os.environ["TELEGRAM_SCAN_PATH"] = previous_snapshot
        blocked.clear()
        today = dt.date.today()
        programs = [reward_hunter.inspect(p, set(snapshot["program_mentions"]), today) for p in reward_hunter.catalog()]
        # Public evidence and templates for drafts are kept alongside local progress.
        with contextlib.closing(sqlite3.connect(directory / "rewards.sqlite3")) as db:
            reward_ledger.observe(db, [*programs, *leads])
            progress = reward_ledger.summary(db)
        report = {"checked_utc": snapshot["generated_utc"], "programs": programs, "leads": leads, "progress": progress}
        agent.save(directory / "reward-report.json", json.dumps(report, ensure_ascii=False, indent=2))
        agent.save(directory / "reward-report.md", reward_hunter.markdown(report))
        status["rewards"] = {"programs": len(programs), "leads": len(leads),
                             "checked": sum(p["status"] != "found" for p in programs),
                             "available": sum(p["status"] == "available" for p in programs),
                             "telegram_mentions": len(snapshot["program_mentions"]),
                             "real_earnings_usd": progress["real_earnings_usd"]}
        events = reward_hunter.events(programs, today)
        status["delivery"] = notify(directory, snapshot, recipient, events)
        if status["coverage"]["failed"] or "v3_error" in status:
            return 1
        return 0 if status["delivery"] in {"Отправлено", NO_REPEAT} else 2
    except Exception as exc:
        status["error"] = type(exc).__name__
        return 1
    finally:
        status["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        agent.save(directory / "monitor-status.json", json.dumps(status, ensure_ascii=False, indent=2))
        handler = RotatingFileHandler(directory / "monitor.log", maxBytes=256_000, backupCount=2, encoding="utf-8")
        try:
            handler.emit(logging.LogRecord(__name__, logging.INFO, __file__, 0,
                                          json.dumps(status, ensure_ascii=False), (), None))
        finally:
            handler.close()


def run():
    if os.name != "nt":
        raise RuntimeError("Windows required")
    import msvcrt

    directory = scanner.private_directory()
    # A manual scan may overlap the task, even when Task Scheduler ignores overlapping triggers.
    with (directory / "monitor.lock").open("ab") as lock:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 3
        return scan_and_report(directory)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    raise SystemExit(run())
