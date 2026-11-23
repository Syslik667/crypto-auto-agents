import datetime as dt
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from game_rewards import model, server, sources, store, telegram
from intelligence.telegram_client import TelegramRejected


TODAY = dt.date(2026, 11, 23)


def example(**changes):
    fields = dict(platform="freecash", title="Puzzle fixture", device="android", germany="yes", deposit="no",
                  purchase="no", fee_mode="deducted", fee="2", minimum="10", kyc="yes", currency="EUR",
                  cost="0", minutes_min="60", minutes_max="120", probability="50",
                  deadline="2026-11-25", payout_method="PayPal", reviewed=True, milestones=[
                      {"label": "Level 5", "amount": "8"}, {"label": "Level 10", "amount": "12"}])
    fields.update(changes)
    return fields


class ModelTests(unittest.TestCase):
    def test_estimate_uses_net_reward_time_and_likelihood(self):
        a = model.analyze(model.normalize(example()), TODAY)
        self.assertEqual(a["rates"], ["4.50", "9.00"])
        self.assertTrue(a["zero_cost"])
        self.assertTrue(a["eligible"])

    def test_unknown_cost_region_and_expired_offers_are_excluded(self):
        for change in ({"germany": "unknown"}, {"fee_mode": "unknown"}, {"deposit": "yes"},
                       {"purchase": "unknown"}, {"deadline": "2026-11-22"},
                       {"title": "Casino bonuses"}, {"reviewed": False}):
            with self.subTest(change=change):
                a = model.analyze(model.normalize(example(**change)), TODAY)
                self.assertFalse(a["eligible"])
                self.assertTrue(a["reasons"])
        self.assertIsNone(model.analyze(model.normalize(example(fee_mode="unknown")), TODAY)["rates"])

    def test_fx_and_relative_deadline(self):
        fields = model.normalize(example(currency="USD", rate_eur="0.8", deadline=None, days=3))
        self.assertIsNone(model.analyze(fields, TODAY)["deadline"])
        a = model.analyze(fields, TODAY, started="2026-11-23")
        self.assertEqual(a["rates"], ["3.60", "7.20"])
        self.assertEqual(a["deadline"], "2026-11-26")

    def test_sanitizes_link_and_rejects_invalid_values(self):
        fields = model.normalize(example(url="https://freecash.com/offer/example?session=secret"))
        self.assertEqual(fields["url"], "https://freecash.com/offer/example")
        for change in ({"url": "https://freecash.com.evil.invalid/"}, {"url": "javascript:alert(1)"},
                       {"probability": "101"}, {"fee": "NaN"}, {"minutes_min": "121"},
                       {"milestones": [{"label": "fake", "amount": "-1"}]}):
            with self.assertRaises(ValueError):
                model.normalize(example(**change))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.db = store.open_db(":memory:")
        self.addCleanup(self.db.close)

    def test_dedup_upload_and_four_balances(self):
        import base64
        screenshot = {"mime": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()}
        offer, created = store.save_offer(self.db, example(screenshot=screenshot), TODAY)
        self.assertTrue(created)
        self.assertEqual(store.save_offer(self.db, example(title="puzzle  fixture"), TODAY), (offer, False))
        self.assertEqual(store.screenshot(self.db, offer)[0], "image/png")
        self.assertEqual(store.summary(self.db, TODAY), {"potential": {"EUR": "20.00"}, "pending": {},
                                                   "confirmed": {}, "withdrawn": {}})
        store.advance(self.db, offer, 0, "pending", True)
        store.advance(self.db, offer, 0, "confirmed", True, "7.50")
        store.advance(self.db, offer, 1, "pending", True)
        self.assertEqual(store.summary(self.db, TODAY)["pending"], {"EUR": "12.00"})
        self.assertEqual(store.summary(self.db, TODAY)["confirmed"], {"EUR": "7.50"})
        store.advance(self.db, offer, 0, "withdrawn", True, "6.50", "USD")
        self.assertEqual(store.summary(self.db, TODAY)["withdrawn"], {"USD": "6.50"})
        self.assertEqual(store.summary(self.db, TODAY)["confirmed"], {})
        with self.assertRaises(ValueError):
            store.advance(self.db, offer, 0, "withdrawn", True, "6.50", "USD")

    def test_updates_and_rejection_log_preserve_tracking(self):
        offer, _ = store.save_offer(self.db, example(deposit="unknown"), TODAY)
        self.assertTrue(store.journal(self.db))
        store.save_offer(self.db, example(), TODAY)
        self.assertEqual(store.list_offers(self.db, TODAY)[0]["id"], offer)
        with self.assertRaises(ValueError):
            store.advance(self.db, offer, 0, "confirmed", True, "7")
        with self.assertRaises(ValueError):
            store.advance(self.db, offer, 0, "pending", False)
        store.advance(self.db, offer, 0, "pending", True)
        with self.assertRaises(ValueError):
            store.save_offer(self.db, example(milestones=[{"label": "Changed", "amount": "80"}]), TODAY)
        self.assertEqual(store.summary(self.db, TODAY)["pending"]["EUR"], "8.00")

    def test_reminders_deduplicate_and_keep_ambiguous_reservations(self):
        offer, _ = store.save_offer(self.db, example(), TODAY)
        store.start(self.db, offer, TODAY)
        events = telegram.reminders(store.list_offers(self.db, TODAY), TODAY)
        self.assertEqual(len(events), 1)
        bot = Mock()
        bot.call.side_effect = [TelegramRejected(), TimeoutError()]
        self.assertIn("отклонил", telegram.deliver(self.db, bot, 44, events))
        self.assertIn("не подтверждена", telegram.deliver(self.db, bot, 44, events))
        self.assertIn("не подтверждена", telegram.deliver(self.db, bot, 44, events))
        self.assertEqual(bot.call.call_count, 2)

    def test_successful_reminder_is_not_sent_twice(self):
        offer, _ = store.save_offer(self.db, example(), TODAY)
        store.start(self.db, offer, TODAY)
        events = telegram.reminders(store.list_offers(self.db, TODAY), TODAY)
        bot = Mock()
        bot.call.return_value = {"message_id": 123}
        self.assertIn("Отправлено", telegram.deliver(self.db, bot, 44, events))
        self.assertIn("новых", telegram.deliver(self.db, bot, 44, events))
        self.assertEqual(bot.call.call_count, 1)

    def test_edit_keeps_identity_and_only_reviewed_offers_count_as_potential(self):
        row, _ = store.save_offer(self.db, example(germany="unknown"), TODAY)
        self.assertEqual(store.summary(self.db, TODAY)["potential"], {})
        self.assertEqual(store.save_offer(self.db, example(id=row, title="Renamed"), TODAY), (row, False))
        self.assertEqual(store.summary(self.db, TODAY)["potential"], {"EUR": "20.00"})
        self.assertEqual(store.list_offers(self.db, TODAY)[0]["title"], "Renamed")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.httpd = server.Dashboard(0, Path(temp.name))
        worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def send(self, method, path, data=None, **headers):
        client = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)
        try:
            client.request(method, path, json.dumps(data).encode() if data is not None else None, headers)
            response = client.getresponse()
            return response.status, response.read(), dict(response.headers)
        finally:
            client.close()

    def post(self, path, data):
        return self.send("POST", path, data, **{"Content-Type": "application/json", "Origin": self.httpd.origin,
                                              "X-Game-Token": self.httpd.csrf})

    def test_loopback_and_write_token_guards(self):
        status, body, headers = self.send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.httpd.csrf.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.send("GET", "/api/state", Host="other.invalid")[0], 403)
        self.assertEqual(self.send("POST", "/api/offer", example(), Origin=self.httpd.origin)[0], 403)
        self.assertEqual(self.send("POST", "/api/offer", example(), Origin="https://other.invalid",
                                   **{"X-Game-Token": self.httpd.csrf})[0], 403)
        self.assertEqual(json.loads(self.send("GET", "/api/state")[1])["offers"], [])

    def test_json_import_partial_error_progress_and_attachment(self):
        import base64
        raw = b"\xff\xd8\xffimage_fixture"
        photo = {"mime": "image/jpeg", "data": base64.b64encode(raw).decode()}
        fixture = example(screenshot=photo)
        status, data, _ = self.post("/api/import", {"offers": [fixture, example(currency="INVALID")]})
        self.assertEqual(status, 200)
        valid, failed = json.loads(data)["results"]
        offer = valid["id"]
        self.assertIn("error", failed)
        status, body, headers = self.send("GET", f"/api/picture/{offer}")
        self.assertEqual((status, body, headers["Content-Type"]), (200, raw, "image/jpeg"))
        self.assertEqual(self.post("/api/start", {"id": offer, "started": "2020-01-01"})[0], 400)
        self.assertEqual(self.post("/api/progress", {"id": offer, "position": 0, "state": "pending", "attested": True})[0], 200)
        self.assertEqual(json.loads(self.send("GET", "/api/state")[1])["summary"]["pending"], {"EUR": "8.00"})
        self.assertEqual(self.post("/api/offer", example(screenshot={"mime": "image/svg+xml", "data": photo["data"]}))[0], 400)

    def test_public_probe_does_not_read_offers_or_accounts(self):
        reader = Mock(return_value=b"help")
        checks = sources.public_status(reader)
        self.assertEqual(len(checks), len(sources.PLATFORMS))
        self.assertEqual([call.args[0] for call in reader.call_args_list], [p["rules"] for p in sources.PLATFORMS.values()])
        self.assertTrue(all(call.kwargs == {"exact_url": True} for call in reader.call_args_list))
