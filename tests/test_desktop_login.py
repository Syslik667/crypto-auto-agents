import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from local_telegram import vault


@unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("win32crypt"), "local Windows DPAPI")
class VaultTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_failed_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertIsNone(vault.load(directory))
            vault.save(directory, 123, "fake-api-hash", "fake-session")
            blob = (directory / vault.FILENAME).read_bytes()
            for secret in (b"fake-api-hash", b"fake-session"):
                self.assertNotIn(secret, blob)
            with patch.object(Path, "replace", side_effect=PermissionError), self.assertRaises(PermissionError):
                vault.save(directory, 123, "new-hash", "new-session")
            self.assertEqual(vault.load(directory), {"api_id": 123, "api_hash": "fake-api-hash", "session": "fake-session"})
            self.assertEqual(len(list(directory.iterdir())), 1)


@unittest.skipUnless(importlib.util.find_spec("telethon") and importlib.util.find_spec("tkinter"), "desktop extras")
class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_code_and_password(self):
        from telethon import errors
        from local_telegram.login_gui import authorize
        client = AsyncMock()
        client.send_code_request.return_value.phone_code_hash = "challenge"
        client.sign_in.side_effect = [errors.PhoneCodeInvalidError(None), errors.SessionPasswordNeededError(None),
                                      errors.PasswordHashInvalidError(None), None]
        ui = Mock()
        ui.ask = AsyncMock(side_effect=[["bad"], ["12345"], ["wrong"], ["correct"]])
        await authorize(client, "+10000000000", ui)
        client.send_code_request.assert_awaited_once()
        self.assertEqual(client.sign_in.await_args_list[1].kwargs["phone_code_hash"], "challenge")
        self.assertEqual(client.sign_in.await_args.kwargs, {"password": "correct"})

    async def test_cancelled_first_login_does_not_replace_the_vault(self):
        from local_telegram.login_gui import login
        ui = Mock()
        ui.ask = AsyncMock(return_value=["123", "a" * 32, "+10000000000"])
        client = AsyncMock()
        client.session = Mock()
        client.session.save.return_value = "synthetic-session"
        with (patch("local_telegram.login_gui.private_directory", return_value=Path("test")),
              patch.object(vault, "load", return_value=None), patch.object(vault, "save") as save,
              patch("telethon.TelegramClient", return_value=client),
              patch("local_telegram.login_gui.authorize", side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await login(ui)
            save.assert_not_called()
            client.disconnect.assert_awaited_once()

    async def test_first_login_persists_only_app_credentials_and_authorized_session(self):
        from local_telegram.login_gui import login
        ui = Mock()
        ui.ask = AsyncMock(return_value=["123", "a" * 32, "+10000000000"])
        client = AsyncMock()
        client.is_user_authorized.return_value = True
        client.session = Mock()
        client.session.save.return_value = "synthetic-session"
        with (patch("local_telegram.login_gui.private_directory", return_value=Path("test")),
              patch.object(vault, "load", return_value=None), patch.object(vault, "save") as save,
              patch("telethon.TelegramClient", return_value=client), patch("local_telegram.login_gui.authorize")):
            await login(ui)
        save.assert_called_once_with(Path("test"), 123, "a" * 32, "synthetic-session")

    async def test_saved_login_reuses_credentials_without_resending_code(self):
        from local_telegram.login_gui import login
        ui = Mock()
        ui.ask = AsyncMock(return_value=[])
        client = AsyncMock()
        client.is_user_authorized.return_value = True
        client.session = Mock()
        client.session.save.return_value = "saved"
        with (patch("local_telegram.login_gui.private_directory", return_value=Path("test")),
              patch.object(vault, "load", return_value=dict(api_id=123, api_hash="hash", session="saved")),
              patch("telethon.sessions.StringSession"), patch("telethon.TelegramClient", return_value=client)):
            await login(ui)
        client.send_code_request.assert_not_called()
        client.disconnect.assert_awaited_once()
