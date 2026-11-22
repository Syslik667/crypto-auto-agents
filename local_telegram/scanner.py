"""Optional local MTProto scanner. Run as python -m local_telegram.scanner."""
import argparse
import asyncio
import contextlib
import datetime as dt
import getpass
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from intelligence.social_monitor import links, publisher_link

TOPICS = re.compile(r"crypto|airdrop|bitcoin|ethereum|blockchain|defi|finance|financial|invest|"
                    r"крипт|аирдроп|эйрдроп|биткоин|эфириум|блокчейн|финанс|инвест", re.I)
ROOT = Path(__file__).resolve().parents[1]


def private_directory():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise ValueError("MTProto scanner is local-only")
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    path = (base / "CryptoIntelligence" / "telegram").resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("Session directory must be outside the repository")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    else:
        who = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"])
        sid = re.search(rb'"(S-1-[0-9-]+)"', who)[1].decode("ascii")
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def readable(entity):
    return (getattr(entity, "broadcast", False) and not getattr(entity, "megagroup", False)
            and not getattr(entity, "left", False) and not getattr(entity, "restricted", False)
            and not getattr(entity, "noforwards", False))


async def channels(client, folder):
    if folder is None:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if readable(entity) and TOPICS.search(getattr(entity, "title", "")):
                yield entity
        return
    from telethon import functions
    response = await client(functions.messages.GetDialogFiltersRequest())
    async for entity in folder_channels(client, response.filters, folder):
        yield entity


async def folder_channels(client, filters, folder):
    matches = [f for f in filters if getattr(getattr(f, "title", None), "text", None) == folder]
    if len(matches) != 1:
        raise FolderError("Укажите точное уникальное имя папки.")
    selected = matches[0]
    excluded = {getattr(p, "channel_id", None) for p in getattr(selected, "exclude_peers", [])}
    dynamic = getattr(selected, "broadcasts", False)
    if dynamic and getattr(selected, "exclude_muted", False):
        raise FolderError("Для автоматической категории каналов отключите исключение по звуку или используйте папку с явным списком каналов.")
    for peer in [*selected.pinned_peers, *selected.include_peers]:
        cid = getattr(peer, "channel_id", None)
        if cid is not None and cid not in excluded:
            excluded.add(cid)
            yield await client.get_entity(peer)
    if dynamic:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not readable(entity) or entity.id in excluded:
                continue
            if getattr(selected, "exclude_archived", False) and dialog.archived:
                continue
            if getattr(selected, "exclude_read", False) and not (dialog.unread_count or dialog.dialog.unread_mark):
                continue
            yield entity


class FolderError(ValueError):
    pass


async def scan(client, channel_type, folder=None):
    result = set()
    counts = {"selected_public": 0, "skipped_private": 0, "failed": 0}
    async for entity in channels(client, folder):
        if not isinstance(entity, channel_type) or not readable(entity):
            continue
        if not getattr(entity, "username", None):
            counts["skipped_private"] += 1
            continue
        counts["selected_public"] += 1
        try:
            async for message in client.iter_messages(entity, limit=30, wait_time=1):
                if getattr(message, "noforwards", False) or getattr(message, "fwd_from", None):
                    continue
                content = getattr(message, "message", "") or ""
                for e in getattr(message, "entities", None) or []:
                    content += " " + (getattr(e, "url", "") or "")
                result.update(u for u in links(content) if publisher_link(u))
        except Exception as exc:
            # Respect FloodWait instead of trying another account or bypassing the limit.
            if type(exc).__name__ == "FloodWaitError":
                raise
            counts["failed"] += 1
    return {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "links": sorted(result), "coverage": counts}


async def run(login=False, folder="Крипта", show_chat_id=False):
    directory = private_directory()
    from telethon import TelegramClient, types

    if not sys.stdin.isatty() and (login or not all(os.environ.get(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"))):
        raise SystemExit("Для первого входа нужен локальный терминал. Для фонового запуска передайте API ID и hash через окружение.")
    api_id = int(os.environ.get("TELEGRAM_API_ID") or getpass.getpass("Telegram API ID: "))
    api_hash = os.environ.get("TELEGRAM_API_HASH") or getpass.getpass("Telegram API hash: ")
    client = TelegramClient(str(directory / "account"), api_id, api_hash,
                            receive_updates=False, flood_sleep_threshold=0, request_retries=0)
    client.session.save_entities = False
    try:
        await client.connect()
        if login:
            with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet):
                await client.start(phone=lambda: getpass.getpass("Phone: "),
                                   code_callback=lambda: getpass.getpass("Login code: "),
                                   password=lambda: getpass.getpass("2FA password: "))
        if not await client.is_user_authorized():
            raise ValueError("Local login required")
        if show_chat_id:
            print("Ваш ID для TELEGRAM_CHAT_ID:", (await client.get_me()).id)
        payload = await scan(client, types.Channel, folder)
        target = directory / "public-links.json"
        with tempfile.NamedTemporaryFile(dir=directory, mode="w", encoding="utf-8", delete=False) as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
        Path(out.name).replace(target)
        print("Public-source snapshot saved outside the repository.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Interactive local authorization")
    parser.add_argument("--show-chat-id", action="store_true", help="Print your own private chat ID locally")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--folder", default="Крипта", help="Exact Telegram folder name")
    mode.add_argument("--topics", action="store_true", help="Discover subscriptions by channel title")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.login, None if args.topics else args.folder, args.show_chat_id))
    except FolderError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        print("Scanner stopped · " + type(exc).__name__)
        raise SystemExit(1) from None
