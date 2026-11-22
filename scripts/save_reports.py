"""Publish a completed scan with ordinary fast-forward pushes. Run at the repo root."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

REPORTS = ("reports/latest.json", "reports/LATEST.md", "reports/LATEST.html")
STATE = "data/seen.json"
OUTPUTS = (*REPORTS, STATE)


def git(*args):
    return subprocess.check_output(["git", *args], encoding="utf-8").strip()


def ancestor(older, newer):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer])
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def document(rev, path):
    return json.loads(git("show", f"{rev}:{path}"))


def combine_state(older, newer):
    # Keep the newer observation order last for the scanner's normal retention policy.
    seen = {}
    for state in (older, newer):
        if not isinstance(state["seen"], list) or not all(isinstance(x, str) for x in state["seen"]):
            raise ValueError("Invalid seen history")
        for key in state["seen"]:
            seen.pop(key, None)
            seen[key] = None
    cache = {}
    for state in (older, newer):
        for month, entries in state.get("chainabuse", {}).items():
            merged = cache.setdefault(month, {})
            for address, value in entries.items():
                if value is not None and type(value) is not bool:
                    raise ValueError("Invalid Chainabuse cache")
                # Every key consumes quota. Keep positive evidence, then a completed lookup.
                if address not in merged or (merged[address] is not True and value is not None):
                    merged[address] = value
    return {**older, **newer, "seen": list(seen), "chainabuse": cache}


def reconcile(remote):
    base = git("merge-base", "HEAD", remote)
    changed = set(git("diff", "--name-only", base, remote, "--", *OUTPUTS).splitlines())
    # Never auto-resurrect an output removed upstream.
    for path in changed:
        git("cat-file", "-e", f"{remote}:{path}")
    winner = "HEAD"
    if changed.intersection(REPORTS):
        def timestamp(rev):
            return dt.datetime.strptime(document(rev, REPORTS[0])["time_utc"], "%Y-%m-%d %H:%M")

        if timestamp(remote) >= timestamp("HEAD"):
            winner = remote
    state = None
    if STATE in changed:
        older, newer = (remote, "HEAD") if winner == "HEAD" else ("HEAD", remote)
        state = combine_state(document(older, STATE), document(newer, STATE))

    # Git keeps unrelated upstream edits. Resolve generated files as one snapshot.
    merge = subprocess.run(["git", "merge", "--no-ff", "--no-commit", remote])
    try:
        conflicts = set(git("diff", "--name-only", "--diff-filter=U").splitlines())
        if merge.returncode and (not conflicts or conflicts.difference(OUTPUTS)):
            merge.check_returncode()
        git("restore", "--source", winner, "--staged", "--worktree", "--", *REPORTS)
        if state is not None:
            Path(STATE).write_text(json.dumps(state, indent=2), encoding="utf-8")
            git("add", "--", STATE)
        git("commit", "-m", "Synchronize monitor reports and discovery history")
    except BaseException:
        if Path(git("rev-parse", "--git-path", "MERGE_HEAD")).exists():
            git("merge", "--abort")
        raise


def publish(ref):
    if not ref.startswith("refs/heads/") or git("symbolic-ref", "HEAD") != ref:
        raise ValueError("Expected the checked-out target branch")
    if git("diff", "--cached", "--name-only"):
        raise ValueError("Unexpected staged changes")
    if not all(Path(path).is_file() for path in OUTPUTS):
        raise ValueError("Incomplete scan output; refusing to publish")
    previous = git("rev-parse", "HEAD")
    git("add", "--", *OUTPUTS)
    if not git("diff", "--cached", "--name-only"):
        return
    git("commit", "-m", "Update crypto reports")
    for attempt in range(3):
        git("fetch", "--no-tags", "origin", ref)
        remote = git("rev-parse", "FETCH_HEAD")
        if ancestor("HEAD", remote):
            return  # The remote accepted a push even if its response was lost.
        if not ancestor(previous, remote):
            raise RuntimeError("Remote history diverged from the observed branch")
        if attempt and remote == previous:
            raise RuntimeError("Push failed without a concurrent branch update; see Git's error above")
        if not ancestor(remote, "HEAD"):
            reconcile(remote)
        if subprocess.run(["git", "push", "origin", f"HEAD:{ref}"]).returncode == 0:
            return
        previous = remote
    # Also handle a lost acknowledgement on the last allowed attempt.
    git("fetch", "--no-tags", "origin", ref)
    if not ancestor("HEAD", "FETCH_HEAD"):
        raise RuntimeError("Report push still unsuccessful after three attempts")


if __name__ == "__main__":
    publish(sys.argv[1])
