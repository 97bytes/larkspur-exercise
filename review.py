#!/usr/bin/env python3
"""review.py: Larkspur's client review of your team's repository.

    python3 review.py            # start of session two: everyone runs this
    python3 review.py --again    # make a new review, even though the team has one

Everyone on the team runs the same command. The first run on a team reads the
team version of the repository, asks Claude for the review (about 30 seconds),
writes FEEDBACK.md and sends it to the team repo. Every later run finds that
file there and brings it down: one review per team, the same file on every
computer.

Before any of that it checks that your key to Claude works, so a problem with
the key shows up now and not halfway through a build.

It never runs your code. It reads the files, makes one call to Claude with your
key, and the only file it writes or sends is FEEDBACK.md.

Design rule, the same as pod_sync.py: never ends on a stack trace, every failure
names the fix.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pod_sync  # noqa: E402
from pod_sync import RULE, ahead_behind, fail, fetch, git, last_line, upstream_ref  # noqa: E402

OUT = "FEEDBACK.md"
MESSAGE = "Client review from Larkspur"
# What the review reads. Changes to these that the team does not have mean the
# review would be of one person's copy, not of the team version.
TEAM_FILES = ["agent.py", "PITCH.md", "TEAM.md", "evals/cases.json"]
TAKE = "python3 pod_sync.py --take-canon"


def team_has_review(ref):
    code, _ = git("cat-file", "-e", "%s:%s" % (ref, OUT), timeout=15)
    return code == 0


def catch_up(ref):
    """Bring this copy level with the team repo. Only ever a fast-forward."""
    ahead, behind = ahead_behind(ref)
    if ahead:
        return False
    if behind:
        code, _ = git("merge", "--ff-only", ref, timeout=60)
        return code == 0
    return True


def unsent_changes():
    out = pod_sync.git_out("status", "--porcelain", "--", *TEAM_FILES)
    return [line.split(None, 1)[-1] for line in out.splitlines() if line.strip()]


def drop_my_review(had_one):
    """Undo our own unpushed review commit, and nothing else."""
    git("rebase", "--abort")
    git("reset", "--soft", "HEAD~1")
    git("reset", "-q", "--", OUT)
    if had_one:
        git("checkout", "--", OUT)
    else:
        try:
            os.remove(os.path.join(HERE, OUT))
        except OSError:
            pass


def send(ref, again):
    """Push the review commit. Returns (status, detail): status is sent,
    beaten (a teammate's review got there first) or failed."""
    out = ""
    for _attempt in range(3):
        code, out = git("push", "origin", "HEAD", timeout=60)
        if code == 0:
            return "sent", ""
        fetch()
        if not again and team_has_review(ref):
            return "beaten", ""
        # --autostash: other files may still be half-edited, the same as a canon
        # push. A rebase that still fails is only "beaten" if the team really has
        # a review now; otherwise saying so would send them to a file nobody has.
        rcode, out = git("rebase", "--autostash", ref, timeout=60)
        if rcode != 0:
            git("rebase", "--abort")
            if not again and team_has_review(ref):
                return "beaten", ""
            return "failed", last_line(out)
    return "failed", last_line(out)


def check_key():
    """setup.py's own live call: the cheapest proof the key works."""
    try:
        import setup
        return setup.check_live_call()
    except Exception as exc:  # noqa: BLE001 - the check must never hide behind a trace
        return type("Check", (), {"ok": False, "fix": "%s: %s" % (type(exc).__name__, exc)})()


def done(lines):
    print("")
    for line in lines:
        print(line)
    print(RULE)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Larkspur's client review of your team's "
                                             "repository. Everyone on the team runs it.")
    ap.add_argument("--again", action="store_true",
                    help="make a new review even though the team already has one")
    args = ap.parse_args()

    print("\n%s\nLARKSPUR CLIENT REVIEW\n%s" % (RULE, RULE))
    problem = pod_sync.preflight()
    if problem:
        return fail(*problem)

    check = check_key()
    if not check.ok:
        return fail("Your key to Claude does not work on this computer yet, so there is no "
                    "review.", *str(check.fix).splitlines())
    print("  ✓ your key to Claude works")

    ok, out = fetch()
    if not ok:
        return fail("Could not reach the team repo.", last_line(out),
                    "Check wifi first, then run  python3 review.py  again.")
    ref = upstream_ref()

    if team_has_review(ref) and not args.again:
        if not catch_up(ref):
            return fail("Your team already has its review, but your copy has commits the team "
                        "does not, so it cannot come down on its own.",
                        "Take the team version (your own agent.py is saved first):",
                        "  " + TAKE,
                        "Then open FEEDBACK.md.")
        return done(["  ✓ your team's review is here: FEEDBACK.md",
                     "",
                     "Someone on your team made it, so this run did not make another one.",
                     "Open FEEDBACK.md in your editor and read it."])

    # From here this run makes the review. It must read the team version.
    if not catch_up(ref):
        return fail("Your copy has commits the team does not have, so a review of it would "
                    "not be a review of the team version.",
                    "Take the team version first (your own agent.py is saved first):",
                    "  " + TAKE,
                    "Then run  python3 review.py  again.")
    # Local edits to the files the review reads (PITCH.md and evals/cases.json are
    # edited by hand all the time) are moved to .workshop/mine/, the same drawer
    # --take-canon uses, and the team version is put back. Refusing here and
    # pointing at --take-canon looped: take-canon only resets agent.py.
    changed = unsent_changes()
    if changed:
        for rel, dest in pod_sync.move_aside(changed):
            print("  your copy of %s is saved at %s" % (rel, dest))
        for rel in changed:
            git("checkout", "--", rel)
        changed = unsent_changes()
    if changed:
        return fail("You have changes the team does not have: %s." % ", ".join(changed),
                    "The review has to read the team version, and these could not be",
                    "moved aside. Show this line to your facilitator.")

    print("  making your team's review: about 30 seconds ...")
    sys.stdout.flush()
    from support.review import review_repo
    had_one = os.path.exists(os.path.join(HERE, OUT))
    result = review_repo(HERE)
    if not result["ok"] or not result["wrote"]:
        return fail("The review could not be made.",
                    *(result["message"] or "no reason was given").splitlines(),
                    "Run  python3 review.py  again. Still stuck? Tell your facilitator.")

    git("add", "--", OUT)
    blocked = pod_sync.key_guard(ref)
    if blocked:
        git("reset", "-q", "--", OUT)
        return fail(*blocked)
    code, _ = git("diff", "--cached", "--quiet", "--", OUT)
    if code == 0:
        return done(["  ✓ your team's review is here: FEEDBACK.md",
                     "",
                     "The new review came back the same as the one the team has, so there was",
                     "nothing to send. Open FEEDBACK.md in your editor and read it."])
    code, out = git("commit", "-q", "-m", MESSAGE, "--", OUT)
    if code != 0:
        git("reset", "-q", "--", OUT)
        return fail("FEEDBACK.md was written, but it could not be saved to git.",
                    last_line(out), "Run  python3 setup.py  and fix the first ✗.")

    status, detail = send(ref, args.again)
    if status == "beaten":
        drop_my_review(had_one)
        if not catch_up(ref) or not os.path.exists(os.path.join(HERE, OUT)):
            return fail("A teammate's review reached the team repo first, and yours was put "
                        "away. Take the team version to get theirs:", "  " + TAKE,
                        "Then run  python3 review.py  again.")
        return done(["  ✓ your team's review is here: FEEDBACK.md",
                     "",
                     "A teammate's review reached the team repo a moment before yours, so you",
                     "now have theirs, the same file as everyone else. Open FEEDBACK.md."])
    if status == "failed":
        drop_my_review(had_one)
        return fail("The review was made, but it could not be sent to the team repo.",
                    detail,
                    "Only people the repo owner invited can send to it. Ask the owner to add",
                    "you on GitHub, or ask a teammate to run  python3 review.py.")
    return done(["  ✓ review made and sent to the team repo: FEEDBACK.md (%d observations)"
                 % result["observations"],
                 "",
                 "Everyone else on your team now runs  python3 review.py  and gets this same",
                 "file. Open FEEDBACK.md in your editor and read it."])


if __name__ == "__main__":
    sys.exit(main())
