"""keyscan.py: find a key to Claude in files before they reach the team repo.

The team repo is public. A key pushed to a public repo can be found and switched
off within minutes, and in a workshop the key is often one key shared by the
whole room, so one push can stop everyone. pod_sync.py runs this before it
commits or pushes anything, and setup.py runs it over the tracked files.

The match is on the shape of a real key, not on the prefix: the prefix on its
own appears in client.py and setup.py, which check keys, and the placeholder in
.env.example is too short to match. The key itself is never printed, only its
last four characters.
"""

from __future__ import annotations

import os
import re
import subprocess

# sk-ant- followed by at least 16 key characters. This covers API keys
# (sk-ant-api03-...) and every other sk-ant- token shape.
KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")


def tail(key: str) -> str:
    """What may be shown of a key: its last four characters."""
    return "…" + key[-4:]


def scan_text(text: str):
    """[(line number, key tail), ...] for every key-shaped string in text."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in KEY_SHAPE.finditer(line):
            hits.append((n, tail(m.group(0))))
    return hits


def _run_git(cwd, *args, timeout=60):
    """Bytes in, text out: a diff can hold any bytes, and a Windows code page
    must never turn a safety check into a crash."""
    try:
        r = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd,
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_diff(diff_text: str):
    """Key-shaped strings on the ADDED lines of `git diff` or `git log -p`
    output. Returns [{"commit", "path", "line", "tail"}, ...]. A commit header
    is expected as a line of the form "\\x00commit <hash>" (see outgoing())."""
    hits = []
    commit, path, line_no, in_header = None, None, 0, False
    for raw in diff_text.splitlines():
        if raw.startswith("\x00commit "):
            commit, path, in_header = raw.split(" ", 1)[1].strip(), None, False
            continue
        if raw.startswith("diff --git "):
            path, in_header = None, True
            continue
        if in_header:
            if raw.startswith("+++ "):
                target = raw[4:]
                path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
                continue
            m = _HUNK.match(raw)
            if m:
                line_no, in_header = int(m.group(1)), False
            continue
        m = _HUNK.match(raw)
        if m:
            line_no = int(m.group(1))
            continue
        if raw.startswith("+"):
            for k in KEY_SHAPE.finditer(raw[1:]):
                hits.append({"commit": commit, "path": path or "?", "line": line_no,
                             "tail": tail(k.group(0))})
            line_no += 1
        elif raw.startswith(" "):
            line_no += 1
    return hits


_DIFF_ARGS = ("-U0", "--no-color", "--no-ext-diff", "--no-textconv")


def staged(cwd):
    """Keys in what `git add` has staged. None if git could not say."""
    out = _run_git(cwd, "diff", "--cached", *_DIFF_ARGS)
    return None if out is None else scan_diff(out)


def outgoing(cwd, remote="origin"):
    """Keys in commits on this laptop that no branch of `remote` has yet, which
    is what a push would send. None if git could not say."""
    if _run_git(cwd, "rev-parse", "--verify", "--quiet", "HEAD") is None:
        return []                         # no commits yet, so nothing to send
    out = _run_git(cwd, "log", "-p", *_DIFF_ARGS, "--format=%x00commit %h",
                   "HEAD", "--not", "--remotes=%s" % remote)
    return None if out is None else scan_diff(out)


def tracked(cwd, max_bytes=2_000_000):
    """Keys in the files git tracks, as they are on disk now (the next commit).
    Returns [{"commit": None, "path", "line", "tail"}, ...], or None if git
    could not list the files."""
    out = _run_git(cwd, "ls-files", "-z", timeout=30)
    if out is None:
        return None
    hits = []
    for rel in out.split("\x00"):
        if not rel:
            continue
        full = os.path.join(cwd, rel)
        try:
            if not os.path.isfile(full) or os.path.getsize(full) > max_bytes:
                continue
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\x00" in data[:8000]:
            continue                      # binary
        for n, t in scan_text(data.decode("utf-8", errors="replace")):
            hits.append({"commit": None, "path": rel, "line": n, "tail": t})
    return hits


def where(hit) -> str:
    """One hit as a plain line: file, line, and the last four characters."""
    text = "%s, line %d (key ending %s)" % (hit["path"], hit["line"], hit["tail"])
    if hit.get("commit"):
        text += ", in your commit %s" % hit["commit"]
    return text


WHY = ("The key to Claude in this workshop is shared by the whole room. The team repo\n"
       "is public, and a key pushed to a public repo can be switched off within\n"
       "minutes. Then it stops working for everyone in the room, not only for you.")
