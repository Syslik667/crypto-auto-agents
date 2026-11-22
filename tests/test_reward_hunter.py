import datetime as dt
from decimal import Decimal
import sqlite3
import unittest

from intelligence import reward_hunter, reward_ledger
from intelligence.telegram_client import TelegramRejected
from local_telegram.windows_monitor import deliver, PENDING


class RewardHunterTests(unittest.TestCase):
    def setUp(self):
        self.program = dict(id="example", title="Example Learn & Earn", category="learn",
                            aliases=["Example Learn & Earn"], url="https://program.example/rewards",
                            evidence={"https://program.example/rewards": ["free enrollment", "rewards paid in USD"]},
                            germany="eligible", kyc="required", fees="none", deposit="none",
                            deadline="2026-11-24", reward="conditional", payout="Bank payout",
                            steps=["Complete the lesson manually"], risks=["Limited slots"], access="public")
        self.today = dt.date(2026, 11, 22)
        self.fetch = lambda _: b"<p>Free <strong>enrollment</strong>. Rewards paid in USD.</p>"

    def inspect(self, fetch=None):
        return reward_hunter.inspect(self.program, {"example"}, self.today, fetch or self.fetch)

    def test_official_evidence_and_deadline(self):
        item = self.inspect()
        self.assertEqual(item["status"], "available")
        self.assertTrue(item["telegram_mention"])
        self.assertEqual(reward_hunter.events([item], self.today)[1][0], "deadline:example:2026-11-24")
        self.assertEqual(self.inspect(lambda _: b"Page changed")["status"], "found")
        self.program["deadline"] = "2026-11-21"
        self.assertEqual(self.inspect()["status"], "checked")

    def test_unknown_terms_fees_and_speculation_block_delivery(self):
        for key, value in (("germany", "unknown"), ("deposit", "required"), ("fees", "unknown"),
                           ("access", "account"), ("reward", "speculative"), ("payout", "")):
            with self.subTest(key=key):
                original = self.program[key]
                self.program[key] = value
                self.assertEqual(reward_hunter.events([self.inspect()], self.today), [])
                self.program[key] = original

    def test_alias_discovery_needs_no_link_and_keeps_only_id(self):
        self.assertEqual(reward_hunter.mentions("News: EXAMPLE learn & earn is open!", [self.program]), {"example"})
        self.assertEqual(reward_hunter.mentions("example learn & earnings", [self.program]), set())

    def test_receipt_must_be_explicit_and_is_counted_once(self):
        with sqlite3.connect(":memory:") as db:
            reward_ledger.observe(db, [self.inspect()])
            self.assertEqual(reward_ledger.summary(db)["real_earnings_usd"], "0.00")
            with self.assertRaises(ValueError):
                reward_ledger.record(db, "example", "received", Decimal("1.00"))
            reward_ledger.record(db, "example", "submitted")
            reward_ledger.record(db, "example", "confirmed")
            reward_ledger.record(db, "example", "received", Decimal("1.00"))
            reward_ledger.observe(db, [self.inspect()])
            with self.assertRaises(ValueError):
                reward_ledger.record(db, "example", "received", Decimal("1.00"))
            self.assertEqual(reward_ledger.summary(db)["real_earnings_usd"], "1.00")

    def test_bot_confirmation_and_dedup(self):
        from unittest.mock import Mock
        bot = Mock()
        bot.call.return_value = {"message_id": 123}
        snapshot = dict(generated_utc="now", coverage=dict(completed=4, messages_read=120, suspicious=0))
        events = reward_hunter.events([self.inspect()], self.today)
        with sqlite3.connect(":memory:") as db:
            self.assertEqual(deliver(db, bot, 111, snapshot, events), "Отправлено")
            self.assertEqual(deliver(db, bot, 111, snapshot, events), "Без повторов")
        bot.call.assert_called_once()

    def test_bot_retries_explicit_rejection_but_not_ambiguous_timeout(self):
        from unittest.mock import Mock

        bot = Mock()
        bot.call.side_effect = [TelegramRejected(), TimeoutError()]
        snapshot = dict(generated_utc="now", coverage=dict(completed=4, messages_read=120, suspicious=0))
        with sqlite3.connect(":memory:") as db:
            self.assertEqual(deliver(db, bot, 111, snapshot, []), "Telegram отклонил отправку")
            self.assertEqual(deliver(db, bot, 111, snapshot, []), PENDING)
            self.assertEqual(deliver(db, bot, 111, snapshot, []), PENDING)
        self.assertEqual(bot.call.call_count, 2)

    def test_catalog_has_unique_program_ids_and_all_categories(self):
        programs = reward_hunter.catalog()
        self.assertEqual(len({p["id"] for p in programs}), len(programs))
        self.assertEqual({p["category"] for p in programs}, set(reward_hunter.CATEGORIES))
        for program in programs:
            self.assertIn(program["url"], program["evidence"])
