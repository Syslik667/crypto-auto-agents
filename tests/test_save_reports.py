import contextlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from intelligence.reports import build_html, build_markdown
from scripts import save_reports


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], encoding="utf-8").strip()


def scan(repo, minute, ids, lookups):
    payload = dict(time_utc=f"2026-11-22 11:{minute:02}", latest=[], top=[], new=[], market=[],
                   contracts=[], health={"test": str(minute)}, real_earnings_usd=0)
    state = {"seen": ids, "chainabuse": {"2026-11": lookups}}
    outputs = (json.dumps(payload), build_markdown(payload), build_html(payload), json.dumps(state))
    for name, content in zip(save_reports.OUTPUTS, outputs, strict=True):
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(content, encoding="utf-8")


class SaveReportsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.remote = root / "origin.git"
        self.peer = root / "peer"
        self.runner = root / "runner"
        git(root, "init", "--bare", "--initial-branch=main", str(self.remote))
        git(root, "clone", str(self.remote), str(self.peer))
        self.identity(self.peer)
        scan(self.peer, 0, ["base"], {})
        self.update("initial")
        git(root, "clone", str(self.remote), str(self.runner))
        self.identity(self.runner)
        self.base = git(self.runner, "rev-parse", "HEAD")

    @staticmethod
    def identity(repo):
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "commit.gpgsign", "false")

    def update(self, message):
        git(self.peer, "add", ".")
        git(self.peer, "commit", "-m", message)
        git(self.peer, "push", "origin", "main")
        return git(self.peer, "rev-parse", "HEAD")

    def publish(self):
        with contextlib.chdir(self.runner):
            save_reports.publish("refs/heads/main")

    def assert_snapshot(self, minute, expected_ids):
        payload = json.loads(git(self.remote, "show", "main:reports/latest.json"))
        self.assertEqual(payload["time_utc"], f"2026-11-22 11:{minute:02}")
        self.assertEqual(git(self.remote, "show", "main:reports/LATEST.md"), build_markdown(payload).strip())
        self.assertEqual(git(self.remote, "show", "main:reports/LATEST.html"), build_html(payload).strip())
        state = json.loads(git(self.remote, "show", "main:data/seen.json"))
        self.assertCountEqual(state["seen"], expected_ids)
        return state["chainabuse"]["2026-11"]

    def test_remote_advances_between_fetch_and_push_twice(self):
        scan(self.runner, 10, ["base", "runner"], {"own": None, "shared": True})
        real_run = subprocess.run
        competing_commits = []

        def race(args, **kwargs):
            if args == ["git", "push", "origin", "HEAD:refs/heads/main"] and len(competing_commits) < 2:
                minute = (5, 15)[len(competing_commits)]
                ids = ["base", "peer1"] if minute == 5 else ["base", "peer1", "peer2"]
                scan(self.peer, minute, ids, {"other": False, "shared": None})
                (self.peer / "user-change.txt").write_text(str(minute))
                competing_commits.append(self.update(f"remote {minute}"))
            return real_run(args, **kwargs)

        with patch("subprocess.run", side_effect=race):
            self.publish()
        self.assertEqual(len(competing_commits), 2)
        self.assertEqual(self.assert_snapshot(15, ["base", "runner", "peer1", "peer2"]),
                         {"own": None, "shared": True, "other": False})
        self.assertEqual(git(self.remote, "show", "main:user-change.txt"), "15")
        for commit in [self.base, *competing_commits]:
            git(self.remote, "merge-base", "--is-ancestor", commit, "main")

    def test_newer_local_scan_with_advanced_remote_state(self):
        scan(self.runner, 20, ["base", "local"], {})
        scan(self.peer, 5, ["base", "concurrent"], {})
        self.update("concurrent")
        self.publish()
        self.assert_snapshot(20, ["base", "local", "concurrent"])

    def test_no_changes_and_permanent_push_failure(self):
        self.publish()
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.base)
        scan(self.runner, 1, ["base", "local"], {})
        actual_run = subprocess.run
        pushes = []

        def deny(args, **kwargs):
            if args[:2] == ["git", "push"]:
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1)
            return actual_run(args, **kwargs)

        with patch("subprocess.run", side_effect=deny), self.assertRaisesRegex(RuntimeError, "without a concurrent"):
            self.publish()
        self.assertEqual(len(pushes), 1)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.base)
        self.assertIn("local", json.loads((self.runner / save_reports.STATE).read_text())["seen"])
