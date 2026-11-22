"""Local Windows prompt for the notification bot credential."""
import os
import re
import tkinter as tk
from tkinter import messagebox, simpledialog

from intelligence.telegram_client import TelegramClient
from . import vault
from .scanner import private_directory

TOKEN = re.compile(r"[1-9]\d{4,19}:[A-Za-z0-9_-]{20,100}")


def main():
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") == "true":
        return
    directory = private_directory()
    root = tk.Tk()
    root.withdraw()
    try:
        while True:
            token = simpledialog.askstring("Уведомления Crypto Cash Hunter",
                "Вставьте токен @CryptoHunterBotai из @BotFather.\nWindows зашифрует его для вашего профиля.",
                parent=root, show="•")
            if not token:
                return
            try:
                token = token.strip()
                if not TOKEN.fullmatch(token):
                    raise ValueError("Invalid Bot API token format")
                bot = TelegramClient(token).call("getMe", {})
                if bot.get("is_bot") is not True or bot.get("username", "").lower() != "cryptohunterbotai":
                    raise ValueError()
                vault.write_protected(directory / "bot.dpapi", token)
            except Exception:
                messagebox.showerror("Telegram", "Не удалось проверить бота или сохранить токен.", parent=root)
                continue
            messagebox.showinfo("Telegram", "Токен сохранён. Откройте личный чат с @CryptoHunterBotai и нажмите Start.", parent=root)
            return
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
