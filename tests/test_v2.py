import datetime as dt
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import agent
from intelligence import airdrop_hunter as hunter
from intelligence import market_analyst as market
from intelligence import net, reports, scam_detector as scam, social_monitor as social

DAY = dt.date(2026, 11, 21)
ADDR = "0x" + "a" * 40
PUB = "https://blog.ethereum.org/launch"
CAT = "https://airdropalert.com/airdrops/launch"


def item(url=PUB, text="Take a quiz. No deposit required.", title="Token rewards", official=True):
    return dict(url=url, title=title, summary=text, refs=set(), published=str(DAY), official=official,
                source="project" if official else social.CATALOG, id=net.digest("legacy" + url),
                status="Условия не проверены", mentions=set())


def evidence(url, **kwargs):
    if "goplus" in url:
        return {"code": 1, "result": {ADDR: {**dict.fromkeys(scam.FLAGS, "0"), "is_open_source": "1",
                                              "sell_tax": "0", "holders": [{"percent": "0.1"}]}}}
    if "dexscreener" in url:
        return [{"chainId": "ethereum", "baseToken": {"address": ADDR}, "liquidity": {"usd": 30000}}]
    if "llama.fi" in url:
        return {"coins": {"ethereum:" + ADDR: {"price": 2}}}
    return {"reports": []}


class HunterTests(unittest.TestCase):
    def test_merge_and_official_terms(self):
        primary = item(text="No deposit required. No KYC required. Waitlist points. Ends November 25, 2026.")
        directory = item(CAT, "Deposit required. Rewards can be withdrawn.", official=False)
        directory["refs"].add(PUB)
        lead, = hunter.group([directory, primary], DAY)
        self.assertEqual(set(lead["sources"].values()), {PUB, CAT})
        self.assertEqual(lead["url"], PUB)
        self.assertIn(directory["id"], lead["aliases"])
        self.assertEqual(lead["terms"]["Депозит"], "заявлено без депозита")
        self.assertEqual(lead["terms"]["KYC"], "заявлено без KYC")
        self.assertEqual(lead["terms"]["Вывод"], hunter.UNKNOWN)
        self.assertIn("денежная сумма не установлена", lead["terms"]["Награда"])
        self.assertEqual(lead["terms"]["Окончание"], "2026-11-25")

    def test_no_title_or_symbol_only_join(self):
        rows = hunter.group([item(), item(CAT, official=False)], DAY)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["token"])
        explicit = item(text="Ethereum contract: " + ADDR)
        self.assertEqual(hunter.group([explicit], DAY)[0]["token"], ("1", ADDR))
        explicit["official"] = False
        self.assertIsNone(hunter.group([explicit], DAY)[0]["token"])

    def test_top_exclusions(self):
        for text in ["not available in Germany", "pay gas", "Ends 2026-11-01", "Deposit required",
                     "withdrawals disabled", "trading volume, staking", "futures"]:
            with self.subTest(text=text):
                lead, = hunter.group([item(text=text)], DAY)
                lead["risk"] = {"status": scam.UNKNOWN}
                self.assertFalse(hunter.rank([lead])[0]["eligible"])
        stale = item()
        stale["published"] = "2020-01-01"
        lead, = hunter.group([stale], DAY)
        lead["risk"] = {"status": scam.UNKNOWN}
        self.assertFalse(hunter.rank([lead])[0]["eligible"])


class DetectorTests(unittest.TestCase):
    def check(self, func, env=None, cache=None):
        with patch.dict(os.environ, env or {}, clear=True), patch.object(scam, "read_json", side_effect=func):
            return scam.inspect("1", ADDR, PUB, {} if cache is None else cache)

    def test_four_risk_levels(self):
        self.assertEqual(self.check(evidence, {"CHAINABUSE_API_KEY": "test"})["status"], scam.LOW)
        self.assertEqual(self.check(evidence)["status"], scam.UNKNOWN)
        for field, expected in [("is_mintable", scam.REVIEW), ("is_honeypot", scam.HIGH)]:
            def changed(url, **kwargs):
                row = evidence(url)
                if "goplus" in url:
                    row["result"][ADDR][field] = "1"
                return row
            risk = self.check(changed)
            self.assertEqual(risk["status"], expected)
            self.assertIn(scam.FLAGS[field], risk["reasons"])
            self.assertEqual(len(risk["sources"]), 5)

    def test_wrong_chain_pair_and_malformed_response(self):
        def wrong(url, **kwargs):
            row = evidence(url)
            if "dexscreener" in url:
                row[0]["chainId"] = "base"
            if "goplus" in url:
                row["result"] = {}
            return row
        result = self.check(wrong, {"CHAINABUSE_API_KEY": "test"})
        self.assertEqual(result["status"], scam.UNKNOWN)
        self.assertTrue(any("нет ликвидности" in s for s in result["reasons"]))

    def test_complaint_limit_counts_failures_and_caches(self):
        with patch.dict(os.environ, {"CHAINABUSE_API_KEY": "secret"}), patch.object(scam, "read_json", side_effect=TimeoutError) as get:
            cache = {str(i): None for i in range(9)}
            with self.assertRaises(TimeoutError):
                scam.complaints("1", ADDR, cache)
            self.assertIsNone(scam.complaints("1", ADDR, cache)[0])
            self.assertIsNone(scam.complaints("1", "other", cache)[0])
            self.assertEqual(get.call_count, 1)

    def test_reported_address_does_not_export_reports(self):
        def complaint(url, **kwargs):
            return [{"victim": "PRIVATE-CANARY"}] if "chainabuse" in url else evidence(url)
        result = self.check(complaint, {"CHAINABUSE_API_KEY": "SECRET-CANARY"})
        self.assertEqual(result["status"], scam.REVIEW)
        self.assertNotIn("CANARY", json.dumps(result))


class PublicOnlyTests(unittest.TestCase):
    def test_public_social_pointer_discovers_official_post_outside_feed(self):
        url = "https://blog.kraken.com/campaign/quiz"
        post = {"status": "publish", "link": url + "/", "date_gmt": str(DAY),
                "title": {"rendered": "Quiz rewards"}, "content": {"rendered": "No deposit required"}}
        with (patch.dict(os.environ, {}, clear=True), patch.object(social, "SOURCES", {}),
              patch.object(social, "reddit", return_value={url}), patch.object(social, "read_json", return_value=[post])):
            health = {}
            article, = social.collect(health)
            self.assertEqual(article["url"], url)
            self.assertTrue(article["official"])
            self.assertEqual(article["mentions"], {"Reddit"})
            post["link"] = url + "-unrelated"
            self.assertEqual(social.collect({}), [])
            post["link"] = url
            post["content"]["protected"] = True
            self.assertEqual(social.collect({}), [])

    def test_telegram_requires_matching_unprotected_channel(self):
        env = dict(TELEGRAM_SOURCE_BOT_TOKEN="secret", TELEGRAM_PUBLIC_CHANNEL="@publicchannel")
        chat = dict(type="channel", username="publicchannel", id=10,
                    pinned_message={"chat": {"id": 10}, "text": PUB + " PRIVATE-CANARY"})
        with patch.dict(os.environ, env), patch.object(social, "read_json", return_value={"ok": True, "result": chat}):
            self.assertEqual(social.telegram(), {PUB})
            chat["has_protected_content"] = True
            health = {}
            self.assertEqual(social.telegram(health), set())
            self.assertEqual(health["Telegram @publicchannel"], "ValueError")

    def test_reddit_public_only(self):
        env = dict(REDDIT_CLIENT_ID="id", REDDIT_CLIENT_SECRET="s", REDDIT_USER_AGENT="app")
        listing = {"data": {"children": [{"data": {"subreddit": "ethereum", "subreddit_type": kind, "url": url}}
                                         for kind, url in [("public", PUB), ("private", CAT)]]}}
        with patch.dict(os.environ, env), patch.object(social, "read_json", side_effect=[{"access_token": "t"}, listing]):
            self.assertEqual(social.reddit(), {PUB})

    def test_discord_private_channel_not_read(self):
        channel = "123456789012345678"
        info = dict(type=5, guild_id="1", permission_overwrites=[dict(id="1", type=0, allow="0", deny="1024")])
        guild = dict(features=["DISCOVERABLE"], id="1", roles=[dict(id="1", permissions="1024")])
        env = dict(DISCORD_BOT_TOKEN="t", DISCORD_PUBLIC_ANNOUNCEMENT_ID=channel)
        with patch.dict(os.environ, env), patch.object(social, "read_json", side_effect=[info, guild]) as get:
            with self.assertRaises(ValueError):
                social.discord()
            self.assertEqual(get.call_count, 2)
        info["permission_overwrites"] = []
        messages = [dict(channel_id=channel, flags=1, content=PUB), dict(channel_id=channel, flags=0, content=CAT)]
        with patch.dict(os.environ, env), patch.object(social, "read_json", side_effect=[info, guild, messages]):
            self.assertEqual(social.discord(), {PUB})

    def test_x_only_public_org_posts(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(social, "read_json", return_value={"type": "rich", "html": PUB}) as get:
            self.assertIsNone(social.x_public(["https://x.com/random/status/1234"]))
            self.assertIn(PUB, social.x_public(["https://x.com/ethereum/status/1234"]))
            self.assertEqual(get.call_count, 1)

    def test_output_links_and_errors(self):
        for url in ["javascript:alert(1)", "https://alice:password@evil.invalid/post", "http://example.org"]:
            self.assertFalse(reports.safe_href(url))
        self.assertEqual(net.canonical(PUB + "/?token=CANARY#chat"), PUB)
        self.assertNotIn("CANARY", net.failure(ValueError("CANARY")))
        self.assertNotIn("CANARY", reports.md_link("a ](x)", PUB + "?token=CANARY"))

    def test_host_circuit_on_limit(self):
        err = urllib.error.HTTPError(PUB, 429, "PRIVATE-CANARY", {}, None)
        with patch.object(net, "blocked", set()), patch("urllib.request.build_opener") as build:
            build.return_value.open.side_effect = err
            with self.assertRaises(urllib.error.HTTPError):
                net.fetch(PUB)
            with self.assertRaises(RuntimeError):
                net.fetch(PUB + "/another")
            self.assertEqual(build.call_count, 1)

    def test_market_includes_only_recent_official_news(self):
        row = dict(name="Ethereum", symbol="ETH")
        posts = [item(text="ETH news"), item(CAT, "ETH rumour", official=False), item(text="unrelated")]
        market.compare([row], posts, DAY)
        self.assertEqual(row["news"], [{"title": "Token rewards", "url": PUB}])


class EndToEnd(unittest.TestCase):
    def test_total_outage_preserves_previous_reports_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / "seen.json"
            state.write_text('{"seen": ["old"]}')
            report = root / "LATEST.md"
            report.write_text("previous successful report")
            with (patch.object(agent, "REPORTS", root), patch.object(agent, "DATA", root),
                  patch.object(agent, "STATE_PATH", state), patch.object(agent, "get_market_data", side_effect=TimeoutError),
                  patch.object(social, "collect", return_value=[]), patch.object(agent, "send_telegram") as notify,
                  self.assertRaises(RuntimeError)):
                agent.run()
            self.assertEqual(report.read_text(), "previous successful report")
            self.assertEqual(state.read_text(), '{"seen": ["old"]}')
            notify.assert_not_called()

    def test_v1_migration_top_limit_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / "seen.json"
            rows = [item(PUB + str(i)) for i in range(8)]
            for post in rows:
                post["published"] = dt.datetime.now(dt.timezone.utc).date().isoformat()
            state.write_text(json.dumps({"seen": [rows[0]["id"]]}))
            with (patch.object(agent, "REPORTS", root / "reports"), patch.object(agent, "DATA", root),
                  patch.object(agent, "STATE_PATH", state), patch.object(agent, "get_market_data", side_effect=TimeoutError),
                  patch.object(social, "collect", return_value=rows), patch.object(agent, "send_telegram", return_value="off"),
                  patch.object(scam, "read_json", side_effect=TimeoutError), patch.dict(os.environ, {}, clear=True)):
                first = agent.run()
                self.assertEqual((len(first["new"]), len(first["top"])), (7, 5))
                html = (agent.REPORTS / "LATEST.html").read_text(encoding="utf-8")
                self.assertTrue(all(post["url"] in html for post in rows))
                second = agent.run()
                self.assertEqual(second["new"], [])
                self.assertEqual(second["health"]["CoinGecko"], "TimeoutError")
                self.assertEqual(len(second["latest"]), 8)
                self.assertEqual(json.loads((agent.REPORTS / "latest.json").read_text(encoding="utf-8")), second)

    def test_invalid_state_never_resets_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "seen.json"
            path.write_text("{broken")
            with patch.object(agent, "STATE_PATH", path), self.assertRaises(ValueError):
                agent.read_state()
            self.assertEqual(path.read_text(), "{broken")
