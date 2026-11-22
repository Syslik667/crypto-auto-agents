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

from intelligence.social_monitor import links, publisher_link, TRIGGERS
from intelligence.reward_hunter import catalog, mentions
from local_telegram import vault

TOPICS = re.compile(r"crypto|airdrop|bitcoin|ethereum|blockchain|defi|finance|financial|invest|"
                    r"крипт|аирдроп|эйрдроп|биткоин|эфириум|блокчейн|финанс|инвест", re.I)
ROOT = Path(__file__).resolve().parents[1]
PROMOTION = re.compile(r"а[ий]рдроп|эйрдроп|раздач|розыгрыш|награ[дт]|бонус", re.I)
SCAM = re.compile(
    r"(?:(?:enter|provide|send|share|введите|укажите|пришлите|отправьте)\s+(?:your\s+|вашу?\s+)?"
    r"(?:seed[- ]?phrase|private key|recovery phrase|сид[- ]?фраз\w*|приватн\w+\s+ключ))"
    r"|(?:pay|send|переведите|оплатите).{0,55}(?:unlock your reward|release your reward|для получения приза)"
    r"|(?:guaranteed (?:profit|returns?)|гарантированн\w+ (?:доход|прибыл))", re.I)


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
        who = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"],
                                      creationflags=subprocess.CREATE_NO_WINDOW)
        sid = re.search(rb'"(S-1-[0-9-]+)"', who)[1].decode("ascii")
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return path


def readable(entity):
    return (getattr(entity, "broadcast", False) and not getattr(entity, "megagroup", False)
            and not getattr(entity, "left", False) and not getattr(entity, "restricted", False)
            and not getattr(entity, "noforwards", False))


async def channels(client, folder, counts):
    if folder is None:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if readable(entity) and TOPICS.search(getattr(entity, "title", "")):
                yield entity
        return
    from telethon import functions
    response = await client(functions.messages.GetDialogFiltersRequest())
    async for entity in folder_channels(client, response.filters, folder, counts):
        yield entity


async def folder_channels(client, filters, folder, counts=None):
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
            try:
                entity = await client.get_entity(peer)
            except Exception as exc:
                if type(exc).__name__ != "ChannelPrivateError":
                    raise
                if counts is not None:
                    counts["skipped_private"] += 1
                continue
            yield entity
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
    program_mentions = set()
    programs = catalog() if folder else []
    counts = {"selected_public": 0, "skipped_private": 0, "failed": 0, "completed": 0,
              "messages_read": 0, "promotions": 0, "suspicious": 0}
    async for entity in channels(client, folder, counts):
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
                counts["messages_read"] += 1
                content = getattr(message, "message", "") or ""
                if TRIGGERS.search(content) or PROMOTION.search(content):
                    counts["promotions"] += 1
                if SCAM.search(content):
                    counts["suspicious"] += 1
                    continue
                program_mentions.update(mentions(content, programs))
                for e in getattr(message, "entities", None) or []:
                    content += " " + (getattr(e, "url", "") or "")
                result.update(u for u in links(content) if publisher_link(u))
            counts["completed"] += 1
        except Exception as exc:
            # Respect FloodWait instead of trying another account or bypassing the limit.
            if type(exc).__name__ in {"FloodWaitError", "FloodPremiumWaitError"}:
                raise
            counts["skipped_private" if type(exc).__name__ == "ChannelPrivateError" else "failed"] += 1
    return {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "folder": folder,
            "links": sorted(result), "coverage": counts, "program_mentions": sorted(program_mentions)}


async def run(login=False, folder="Крипта", show_chat_id=False, return_recipient=False):
    directory = private_directory()
    from telethon import TelegramClient, types
    from telethon.sessions import StringSession

    saved = vault.load(directory) if os.name == "nt" else None
    if (sys.stdin is None or not sys.stdin.isatty()) and (
        login or (not saved and not all(os.environ.get(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")))
    ):
        raise ValueError("Local login required")
    api_id = saved["api_id"] if saved else int(os.environ.get("TELEGRAM_API_ID") or getpass.getpass("Telegram API ID: "))
    api_hash = saved["api_hash"] if saved else os.environ.get("TELEGRAM_API_HASH") or getpass.getpass("Telegram API hash: ")
    session = StringSession(saved["session"]) if saved else str(directory / "account")
    client = TelegramClient(session, api_id, api_hash,
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
        if saved and client.session.save() != saved["session"]:
            vault.save(directory, api_id, api_hash, client.session.save())
        if show_chat_id:
            print("Ваш ID для TELEGRAM_CHAT_ID:", (await client.get_me()).id)
        payload = await scan(client, types.Channel, folder)
        target = directory / "public-links.json"
        with tempfile.NamedTemporaryFile(dir=directory, mode="w", encoding="utf-8", delete=False) as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
        Path(out.name).replace(target)
        print("Public-source snapshot saved outside the repository.")
        if return_recipient:
            me = await client.get_me()
            if not me or me.bot:
                raise ValueError("A local user account is required")
            return payload, me.id
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
