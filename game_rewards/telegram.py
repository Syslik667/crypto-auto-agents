"""Send deadline reminders to the owner of the existing local Telegram login."""
import asyncio
import hashlib

from intelligence.telegram_client import TelegramClient, TelegramRejected
from local_telegram import vault

UNCONFIRMED = "Доставка не подтверждена. Повтор заблокирован."


def reminders(items, today):
    from .model import date

    events = []
    for item in items:
        end = date(item["analysis"]["deadline"])
        if (not item["started"] or not item["analysis"]["eligible"] or not end
                or not 0 <= (end - today).days <= 2):
            continue
        pending_step = next((m["label"] for m in item["milestones"] if m["state"] == "potential"), None)
        if pending_step:
            event = f"{item['id']}:{end.isoformat()}"
            message = (f"{item['title']} · до {end:%d.%m.%Y}\n"
                       f"Следующий этап: {pending_step}\n"
                       "Проверьте точное время и зачёт в аккаунте площадки.")
            events.append((event, message))
    return events


def deliver(db, bot, recipient, events):
    owner = hashlib.sha256(str(recipient).encode()).hexdigest()
    sent = []
    lines = ["Game Reward Agent · дедлайны"]
    with db:
        db.execute("BEGIN IMMEDIATE")
        for event, message in events:
            if len(sent) >= 5:
                break
            if db.execute("INSERT OR IGNORE INTO reminders VALUES (?,?,'pending')", (owner, event)).rowcount:
                sent.append((owner, event))
                lines.append(message)
        if not sent:
            waiting = db.execute("SELECT 1 FROM reminders WHERE recipient=? AND status='pending'", (owner,)).fetchone()
            return UNCONFIRMED if waiting else "Нет новых напоминаний."
    try:
        result = bot.call("sendMessage", {
            "chat_id": str(recipient), "text": "\n\n".join(lines), "protect_content": "true",
            "link_preview_options": '{"is_disabled":true}',
        })
        if not isinstance(result, dict) or not result.get("message_id"):
            return UNCONFIRMED
    except TelegramRejected:
        with db:
            db.executemany("DELETE FROM reminders WHERE recipient=? AND event=?", sent)
        return "Telegram отклонил сообщение. Проверьте личный чат с ботом."
    except Exception:
        return UNCONFIRMED
    with db:
        db.executemany("UPDATE reminders SET status='sent' WHERE recipient=? AND event=?", sent)
    return f"Отправлено напоминаний: {len(sent)}."


async def owner_account(saved):
    from telethon import TelegramClient as UserClient
    from telethon.sessions import StringSession

    client = UserClient(StringSession(saved["session"]), saved["api_id"], saved["api_hash"],
                        receive_updates=False, timeout=5, connection_retries=0, request_retries=0,
                        auto_reconnect=False, flood_sleep_threshold=0)
    client.session.save_entities = False
    try:
        await client.connect()
        me = await client.get_me()
        if not me or me.bot:
            raise ValueError("Local user required")
        return me.id
    finally:
        await client.disconnect()


def notify(db, directory, events):
    if not events:
        return "Нет подходящих дедлайнов в ближайшие два дня."
    try:
        saved = vault.load(directory)
        credential = vault.read_protected(directory / "bot.dpapi")
        if not saved or not credential:
            return "Подключите Telegram через TELEGRAM_LOGIN.bat и TELEGRAM_BOT_SETUP.bat."
        recipient = asyncio.run(asyncio.wait_for(owner_account(saved), timeout=12))
        bot = TelegramClient(credential)
        identity = bot.call("getMe", {})
        if identity.get("is_bot") is not True or identity.get("username", "").lower() != "cryptohunterbotai":
            return "В защищённом хранилище настроен другой бот."
        return deliver(db, bot, recipient, events)
    except Exception as exc:
        return "Не удалось подключиться к Telegram · " + type(exc).__name__
