"""Bounded Bot API access. Credentials and response bodies stay in memory."""
import os
import re
from urllib.parse import urlencode

from .net import failure, read_json


class TelegramClient:
    def __init__(self, token, reader=read_json):
        self._token = token
        self._reader = reader

    def call(self, method, values):
        if method not in {"getChat", "sendMessage", "getMe"}:
            raise ValueError("Unsupported Telegram method")
        try:
            data = self._reader(f"https://api.telegram.org/bot{self._token}/{method}",
                                data=urlencode(values).encode())
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise ValueError("Telegram rejected request")
            return data["result"]
        except Exception:
            raise RuntimeError("Telegram API request failed") from None

    def pinned_links(self, channel, extract_links):
        chat = self.call("getChat", {"chat_id": "@" + channel})
        if (chat.get("type") != "channel" or chat.get("username", "").lower() != channel.lower()
                or chat.get("has_protected_content")):
            raise ValueError("Public unprotected channel required")
        post = chat.get("pinned_message", {})
        if (not post or post.get("has_protected_content") or post.get("forward_origin")
                or post.get("chat", {}).get("id") != chat.get("id")):
            return set()
        text = " ".join([post.get("text", ""), post.get("caption", ""),
                         *(e.get("url", "") for e in post.get("entities", []) + post.get("caption_entities", []))])
        return extract_links(text)


def monitor(extract_links, health, reader=read_json):
    token = os.environ.get("TELEGRAM_SOURCE_BOT_TOKEN", "").strip()
    names = os.environ.get("TELEGRAM_PUBLIC_CHANNELS") or os.environ.get("TELEGRAM_PUBLIC_CHANNEL", "")
    if not token or not names.strip():
        return None
    if token == os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        raise ValueError("Use a separate source bot")
    channels = list(dict.fromkeys(n.lstrip("@").lower() for n in re.split(r"[\s,]+", names.strip())))
    if len(channels) > 5 or any(not re.fullmatch(r"[a-z0-9_]{5,32}", n) for n in channels):
        raise ValueError("Configure up to five public channel usernames")
    found = set()
    client = TelegramClient(token, reader)
    for channel in channels:
        try:
            found.update(client.pinned_links(channel, extract_links))
            health["Telegram @" + channel] = "OK · проверено закрепление"
        except Exception as exc:
            health["Telegram @" + channel] = failure(exc)
    return found


def notify(payload):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return "Не настроен (необязательно)"
    if not re.fullmatch(r"[1-9]\d{0,19}", chat):
        return "Требуется ID личного чата"
    lines = ["Crypto Cash Hunter v3 · " + payload["time_utc"] + " UTC",
             f"Новых публикаций {len(payload['new'])} · заработано $0"]
    for coin in payload["market"]:
        lines.append(f"{coin['symbol']} · {coin['usd']} USD · {coin['change24h']}% за 24ч")
    for item in payload.get("top", [])[:5]:
        block = ["", item["title"][:100], item["url"], item["risk"]["status"],
                 item["risk"]["reasons"][0][:200]]
        block.extend(s[:200] for s in item.get("guide", {}).get("steps", [])[:2])
        if len("\n".join(lines + block).encode("utf-16-le")) // 2 > 3800:
            break
        lines += block
    try:
        client = TelegramClient(token)
        expected = (os.environ.get("TELEGRAM_EXPECTED_BOT") or "CryptoHunterBotai").lstrip("@")
        me = client.call("getMe", {})
        if me.get("is_bot") is not True or me.get("username", "").lower() != expected.lower():
            return "Ошибка Telegram · username бота отличается от настройки"
        result = client.call("sendMessage", {
            "chat_id": chat, "text": "\n".join(lines), "protect_content": "true",
            "link_preview_options": '{"is_disabled":true}',
        })
        if not isinstance(result, dict) or not result.get("message_id"):
            raise ValueError("Missing delivery confirmation")
        return "Отправлено"
    except Exception:
        return "Ошибка Telegram · проверьте secrets и разрешение боту писать в личный чат"
