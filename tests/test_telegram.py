import json
import os
import unittest
from unittest.mock import Mock, patch

from intelligence import telegram_client as telegram, reward_guide
from intelligence.social_monitor import links


class TelegramTests(unittest.TestCase):
    def test_api_failure_redacts_credentials_and_checks_ok(self):
        for reader in [Mock(side_effect=ValueError("TOKEN-CANARY")), Mock(return_value={"ok": False})]:
            client = telegram.TelegramClient("TOKEN-CANARY", reader)
            with self.assertRaises(RuntimeError) as caught:
                client.call("getChat", {"chat_id": "@channel"})
            self.assertNotIn("CANARY", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_multiple_channels_isolate_failure_and_export_only_links(self):
        reader = Mock(side_effect=[
            ValueError("TOKEN-CANARY"),
            {"ok": True, "result": {"id": 2, "type": "channel", "username": "publictwo",
             "pinned_message": {"chat": {"id": 2}, "text": "PERSON-CANARY https://blog.kraken.com/quiz?secret=CANARY"}}},
        ])
        with patch.dict(os.environ, {"TELEGRAM_SOURCE_BOT_TOKEN": "source",
                                     "TELEGRAM_PUBLIC_CHANNELS": "@publicone, @publictwo"}, clear=True):
            health = {}
            found = telegram.monitor(links, health, reader)
        self.assertEqual(found, {"https://blog.kraken.com/quiz"})
        self.assertNotIn("CANARY", json.dumps([sorted(found), health]))
        self.assertEqual(reader.call_count, 2)

    def test_reject_shared_bot_and_private_chat(self):
        reader = Mock(return_value={"ok": True, "result": {"type": "private", "id": 2}})
        with self.assertRaises(ValueError):
            telegram.TelegramClient("source", reader).pinned_links("publicone", links)
        with patch.dict(os.environ, {"TELEGRAM_SOURCE_BOT_TOKEN": "same", "TELEGRAM_BOT_TOKEN": "same",
                                     "TELEGRAM_PUBLIC_CHANNELS": "@publicone"}, clear=True):
            with self.assertRaises(ValueError):
                telegram.monitor(links, {}, reader)

    def test_notification_delivery_and_failure(self):
        payload = {"time_utc": "2026-11-22 12:00", "market": [], "new": [], "top": []}
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "notification", "TELEGRAM_CHAT_ID": "123"}, clear=True):
            with patch.object(telegram.TelegramClient, "call", side_effect=[
                {"is_bot": True, "username": "CryptoHunterBotai"}, {"message_id": 1}
            ]) as call:
                self.assertEqual(telegram.notify(payload), "Отправлено")
                values = call.call_args.args[1]
                self.assertEqual(values["protect_content"], "true")
                self.assertNotIn("parse_mode", values)
            with patch.object(telegram.TelegramClient, "call", return_value={"is_bot": True, "username": "other"}) as call:
                self.assertIn("username", telegram.notify(payload))
                call.assert_called_once_with("getMe", {})
            with patch.object(telegram.TelegramClient, "call", side_effect=RuntimeError("TOKEN-CANARY")):
                self.assertNotIn("CANARY", telegram.notify(payload))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("Не настроен", telegram.notify(payload))


class RewardGuideTests(unittest.TestCase):
    def item(self):
        return {"url": "https://blog.kraken.com/quiz", "official": True, "exclusions": [],
                "risk": {"status": "Низкий обнаруженный риск"},
                "terms": {"Германия": "заявлено участие", "Депозит": "заявлено без депозита",
                          "Комиссии": "заявлено без комиссии", "KYC": "заявлено без KYC",
                          "Окончание": "2026-12-20", "Вывод": "заявлен, фактически не проверен",
                          "Участие": "пройти викторину", "Награда": "points"}}

    def test_verified_terms_produce_manual_steps_with_source(self):
        item = self.item()
        guide = reward_guide.build(item)
        self.assertEqual(guide["source"], item["url"])
        self.assertEqual(guide["blockers"], [])
        self.assertIn("вручную", guide["steps"][1])
        self.assertEqual(guide["reward"], "points")

    def test_unknown_terms_and_high_risk_block_participation_instructions(self):
        item = self.item()
        item["terms"]["Германия"] = "не указано"
        item["risk"]["status"] = "Высокий риск"
        guide = reward_guide.build(item)
        self.assertEqual(len(guide["blockers"]), 2)
        self.assertNotIn("пройдите викторину", " ".join(guide["steps"]))
