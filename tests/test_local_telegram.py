import datetime as dt
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from local_telegram.scanner import scan, private_directory, folder_channels, FolderError
from intelligence.social_monitor import local_telegram


class Channel(NS):
    pass


class LocalScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_inaccessible_peer_does_not_abort_named_folder(self):
        from local_telegram import scanner

        class ChannelPrivateError(Exception):
            pass

        folder = NS(title=NS(text="Крипта"), pinned_peers=[],
                    include_peers=[NS(channel_id=1), NS(channel_id=2), NS(channel_id=3)])

        class Client:
            async def get_entity(self, peer):
                if peer.channel_id == 1:
                    raise ChannelPrivateError()
                return Channel(id=peer.channel_id, broadcast=True, username="public")

            async def iter_messages(self, entity, **kwargs):
                if entity.id == 2:
                    raise ChannelPrivateError()
                yield NS(message="Example Learn & Earn. PERSONAL CANARY", entities=[])
                yield NS(message="Send your seed phrase https://blog.kraken.com/reward", entities=[])

        with patch.object(scanner, "channels", lambda c, f, counts: folder_channels(c, [folder], f, counts)), \
                patch.object(scanner, "mentions", lambda content, _: {"example"} if "Example" in content else set()):
            payload = await scan(Client(), Channel, "Крипта")
        self.assertEqual(payload["coverage"]["skipped_private"], 2)
        self.assertEqual(payload["coverage"]["completed"], 1)
        self.assertEqual(payload["coverage"]["suspicious"], 1)
        self.assertEqual(payload["program_mentions"], ["example"])
        self.assertEqual(payload["links"], [])
        self.assertNotIn("CANARY", json.dumps(payload))

    async def test_named_folder_resolves_only_included_channel_peers(self):
        from unittest.mock import AsyncMock, Mock
        p = NS(channel_id=10)
        folder = NS(title=NS(text="Крипта"), pinned_peers=[p], include_peers=[p, NS(user_id=1),
                    NS(chat_id=2), NS(channel_id=11)], exclude_peers=[NS(channel_id=11)])
        client = Mock()
        client.get_entity = AsyncMock(return_value="included")
        self.assertEqual([e async for e in folder_channels(client, [folder], "Крипта")], ["included"])
        client.get_entity.assert_awaited_once_with(p)
        for filters in ([], [folder, folder]):
            with self.assertRaises(FolderError):
                _ = [e async for e in folder_channels(client, filters, "Крипта")]
        client.iter_dialogs.assert_not_called()

    async def test_dynamic_folder_obeys_exclusions(self):
        folder = NS(title=NS(text="Крипта"), pinned_peers=[], include_peers=[], broadcasts=True,
                    exclude_archived=True, exclude_read=True, exclude_peers=[NS(channel_id=4)])
        class Client:
            async def iter_dialogs(self):
                for i in range(1, 5):
                    yield NS(entity=Channel(id=i, broadcast=True), archived=i == 2,
                             unread_count=int(i != 3), dialog=NS(unread_mark=False))
        result = [e.id async for e in folder_channels(Client(), [folder], "Крипта")]
        self.assertEqual(result, [1])

    async def test_only_thematic_public_broadcast_history_requested(self):
        public = Channel(title="Crypto news", broadcast=True, username="publicone")
        private = Channel(title="Airdrop private", broadcast=True, username=None)
        group = Channel(title="Crypto group", broadcast=True, megagroup=True, username="group")
        personal = NS(title="Finance friend")
        other = Channel(title="Cooking", broadcast=True, username="cooking")
        calls = []

        class Client:
            async def iter_dialogs(self):
                for entity in [public, private, group, personal, other]:
                    yield NS(entity=entity)

            async def iter_messages(self, entity, **kwargs):
                calls.append(entity)
                yield NS(message="PERSON-CANARY https://blog.kraken.com/quiz?key=CANARY", entities=[])
                yield NS(message="https://blog.kraken.com/private", noforwards=True)

        payload = await scan(Client(), Channel)
        self.assertEqual(calls, [public])
        self.assertEqual(payload["links"], ["https://blog.kraken.com/quiz"])
        self.assertEqual(payload["coverage"]["skipped_private"], 1)
        self.assertNotIn("CANARY", json.dumps(payload))

    def test_actions_never_opens_session(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(ValueError):
                private_directory()

    def test_snapshot_expiry_and_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "public-links.json"
            data = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "links": ["https://blog.kraken.com/quiz", "https://evil.invalid/claim"]}
            p.write_text(json.dumps(data))
            with patch.dict(os.environ, {"TELEGRAM_SCAN_PATH": str(p)}, clear=True):
                self.assertEqual(local_telegram(), {"https://blog.kraken.com/quiz"})
                data["generated_utc"] = "2000-01-01T00:00:00+00:00"
                p.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    local_telegram()
