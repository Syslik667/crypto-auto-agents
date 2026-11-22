"""Interactive local Windows login. All Tk events and network awaits use one thread."""
import asyncio
import logging
import os
import re
import tkinter as tk
from tkinter import ttk

from . import vault
from .scanner import private_directory


class LoginWindow:
    def __init__(self, root):
        root.title("Crypto Cash Hunter · Вход в Telegram")
        root.minsize(430, 230)
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Вход в Telegram", font=("Segoe UI", 16)).pack(anchor="w")
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=390).pack(anchor="w", pady=(8, 12))
        self.form = ttk.Frame(frame)
        self.form.pack(fill="x")

    async def ask(self, message, labels, button):
        self.status.set(message)
        answer = asyncio.get_running_loop().create_future()
        entries = []
        for label, hidden in labels:
            ttk.Label(self.form, text=label).pack(anchor="w")
            entry = ttk.Entry(self.form, show="•" if hidden else "", exportselection=False)
            entry.pack(fill="x", pady=(0, 8))
            entries.append(entry)

        def submit(event=None):
            if not answer.done():
                answer.set_result([entry.get() for entry in entries])

        action = ttk.Button(self.form, text=button, command=submit)
        action.pack(anchor="w")
        for entry in entries:
            entry.bind("<Return>", submit)
        (entries[0] if entries else action).focus_set()
        try:
            return await answer
        finally:
            for widget in self.form.winfo_children():
                widget.destroy()


async def authorize(client, phone, ui):
    from telethon import errors

    sent = await client.send_code_request(phone)
    message = "Введите код, полученный от Telegram."
    while True:
        code, = await ui.ask(message, [("Код входа", True)], "Подтвердить")
        ui.status.set("Проверяем код…")
        try:
            await client.sign_in(phone=phone, code=code.strip(), phone_code_hash=sent.phone_code_hash)
            return
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            message = "Код не подошёл. Проверьте код Telegram и повторите ввод."
        except errors.SessionPasswordNeededError:
            break
    message = "Для аккаунта включена двухэтапная проверка."
    while True:
        password, = await ui.ask(message, [("Пароль 2FA", True)], "Войти")
        ui.status.set("Проверяем пароль…")
        try:
            await client.sign_in(password=password)
            return
        except errors.PasswordHashInvalidError:
            message = "Неверный пароль двухэтапной проверки. Попробуйте ещё раз."


async def login(ui):
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession

    directory = private_directory()
    try:
        saved = vault.load(directory)
    except Exception:
        await ui.ask("Windows не удалось открыть сохранённый вход. Заменить его можно после успешной авторизации.",
                     [], "Войти заново")
        saved = None
    if saved:
        await ui.ask("На этом компьютере есть защищённая сессия.", [], "Проверить вход")
        client = TelegramClient(StringSession(saved["session"]), saved["api_id"], saved["api_hash"],
                                receive_updates=False, flood_sleep_threshold=0)
        try:
            await client.connect()
            if await client.is_user_authorized():
                if client.session.save() != saved["session"]:
                    vault.save(directory, saved["api_id"], saved["api_hash"], client.session.save())
                return
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError):
            pass
        finally:
            await client.disconnect()

    message = "Введите данные приложения с my.telegram.org и номер аккаунта. После входа Windows зашифрует ключи приложения и сессию в вашем профиле."
    while True:
        api_id, api_hash, phone = await ui.ask(message, [("API ID", False), ("API Hash", True),
                                                       ("Номер телефона с кодом страны", False)], "Получить код")
        api_id, api_hash = api_id.strip(), api_hash.strip()
        phone = re.sub(r"[\s()-]", "", phone)
        if (re.fullmatch(r"[1-9][0-9]{0,9}", api_id) and int(api_id) < 2**31
                and re.fullmatch(r"[a-fA-F0-9]{32}", api_hash) and re.fullmatch(r"\+[1-9][0-9]{6,14}", phone)):
            break
        message = "Проверьте числовой API ID, 32 символа API Hash и международный номер с плюсом."
    client = TelegramClient(StringSession(), int(api_id), api_hash, receive_updates=False, flood_sleep_threshold=0)
    try:
        ui.status.set("Подключаемся к Telegram…")
        await client.connect()
        await authorize(client, phone, ui)
        if not await client.is_user_authorized():
            raise RuntimeError("Authorization incomplete")
        vault.save(directory, int(api_id), api_hash, client.session.save())
    finally:
        await client.disconnect()


async def main():
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") == "true":
        raise SystemExit("Окно входа предназначено для локальной Windows.")
    root = tk.Tk()
    ui = LoginWindow(root)
    closed = asyncio.Event()
    root.protocol("WM_DELETE_WINDOW", closed.set)

    async def interact():
        while True:
            try:
                await login(ui)
            except Exception as exc:
                messages = {
                    "PhoneCodeExpiredError": "Срок действия кода истёк.",
                    "ApiIdInvalidError": "Telegram отклонил API ID или API Hash.",
                    "PhoneNumberInvalidError": "Telegram отклонил номер телефона.",
                    "FloodWaitError": f"Telegram просит подождать {getattr(exc, 'seconds', 0)} сек.",
                }
                await ui.ask(messages.get(type(exc).__name__, "Ошибка подключения или защищённого хранилища Windows."),
                             [], "Повторить")
                continue
            await ui.ask("Вход подтверждён. Локальный сканер теперь может использовать сессию.", [], "Закрыть")
            closed.set()
            return

    task = asyncio.create_task(interact())
    try:
        while not closed.is_set() and not task.done():
            root.update()
            await asyncio.sleep(0.025)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            root.destroy()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    asyncio.run(main())
