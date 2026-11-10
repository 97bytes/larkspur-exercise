#!/usr/bin/env python3
"""setup.py: run this until it says READY. That is the whole setup.

    python3 setup.py            # check this seat
    python3 setup.py --fix      # create .venv and install, then re-run
    python3 setup.py --no-live  # skip the live API call (faster re-runs)
    python3 setup.py --key      # paste your key to Claude from the build site; it never shows
    python3 setup.py --day0     # the laptop check before the session: no key, no team repo yet

It checks one thing: that this laptop can do the build. Python, the SDK, a
credential that actually works, git, and a real reach to the team repo. Run it
at your own desk, with time to fix what it finds. Everything it names is a 10
minute fix before the day and a lost build in the room.

Design rule: this script never ends on a stack trace and never ends on
"something went wrong". Every failure names the fix. If you hit one it cannot
explain, that is a bug in the workshop. Say so in the room, because it will be
wrong for someone else too.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RULE = "─" * 66
MIN_PY = (3, 9)
GOOD_PY = (3, 11)
DAY0 = False   # set by main(): the "run this next" hints then stay in Day 0


def again(extra=""):
    """The command to run next, in the mode the person is in."""
    return "python3 setup.py%s%s" % (" --day0" if DAY0 else "", extra)


class Check:
    """One line on the board, with a stable id so a tool can track it."""

    def __init__(self, id, ok, label, fix="", severity="required"):
        self.id, self.ok, self.label, self.fix, self.severity = id, ok, label, fix, severity


def _git(*args, **kwargs):
    """Never let git open an interactive prompt. A password box behind a script
    looks like a hang, and a hang has no fix line."""
    timeout = kwargs.pop("timeout", 20)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo",
               GCM_INTERACTIVE="never")
    try:
        r = subprocess.run(["git"] + list(args), cwd=HERE, capture_output=True,
                           text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def last_line(text):
    return text.splitlines()[-1] if text else "no response"


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def check_python():
    v = sys.version_info
    label = "Python %d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable)
    if v < MIN_PY:
        return Check("python", False, label,
                     "Too old. Install Python 3.11 from python.org, or "
                     "`brew install python@3.11`.")
    if v < GOOD_PY:
        return Check("python", True, label + "  (3.11 preferred, 3.9 works)")
    return Check("python", True, label)


def check_venv():
    """Advisory. What has to be true is that the SDK imports, and the next
    check says so. A venv is how you get there without an IT ticket."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return Check("venv", True, "isolated environment: %s" % os.path.basename(sys.prefix))
    conda = os.environ.get("CONDA_PREFIX")
    if conda and os.path.realpath(sys.executable).startswith(os.path.realpath(conda)):
        return Check("venv", True, "isolated environment: conda (%s)" % os.path.basename(conda))
    if os.path.isdir(os.path.join(HERE, ".venv")):
        return Check("venv", False, ".venv is here but this shell is not in it",
                     "source .venv/bin/activate     (.venv\\Scripts\\activate on Windows)\n"
                     "     Not blocking, as long as the SDK check below is green.",
                     "advisory")
    return Check("venv", False, "running on system Python, no .venv here",
                 "Not blocking, as long as the SDK check below is green. If it is not:\n"
                 "       " + again(" --fix") + "\n"
                 "     creates .venv here and installs into it. Nothing touches your system\n"
                 "     Python, so no admin rights and no IT ticket.",
                 "advisory")


def check_sdk():
    try:
        import anthropic
    except ImportError:
        return Check("sdk", False, "anthropic SDK installed",
                     "Fix: " + again(" --fix") + "\n"
                     "     or: python3 -m pip install -r requirements.txt")
    return Check("sdk", True, "anthropic SDK %s" % getattr(anthropic, "__version__", "?"))


def check_writable():
    """.workshop/ is where your saved gates and bench numbers live. If it
    cannot be written, verify.py has nowhere to put your receipt."""
    target = os.path.join(HERE, ".workshop")
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, ".probe")
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
        return Check("writable", True, "can write to .workshop/ (your gates and bench numbers)")
    except OSError as exc:
        return Check("writable", False, "can write to .workshop/",
                     "%s\n     Move the folder somewhere you own. Desktop or home, not a "
                     "synced or managed directory." % exc)


def check_git_identity():
    code_v, out_v = _git("--version", timeout=15)
    if code_v != 0:
        return Check("gitid", False, "git installed",
                     "The whole session runs out of one shared repo, so git is not optional.\n"
                     "     macOS: xcode-select --install\n"
                     "     Windows: https://git-scm.com/download/win\n"
                     "     Linux: your package manager, e.g. sudo apt install git")
    code_n, name = _git("config", "user.name", timeout=15)
    code_e, email = _git("config", "user.email", timeout=15)
    if code_n != 0 or not name or code_e != 0 or not email:
        return Check("gitid", False, "git identity set",
                     "Your commits would show up as nobody.\n"
                     "       git config --global user.name \"Your Name\"\n"
                     "       git config --global user.email \"you@yourfirm.com\"")
    return Check("gitid", True, "git identity: %s <%s>" % (name, email))


def check_env_ignored():
    """A key in the repo is the one mistake here that follows you home."""
    code, _ = _git("check-ignore", "-q", ".env")
    if code == 127:
        return Check("envleak", True, ".env is gitignored (skipped, git is not installed)",
                     "", "advisory")
    if code != 0:
        return Check("envleak", False, ".env is gitignored",
                     "Your API key would go to GitHub on the next push. Put `.env` in "
                     ".gitignore\n     before you write the file, and say so in the room if "
                     "it was ever pushed.")
    code, out = _git("log", "--all", "--oneline", "--", ".env")
    if code == 0 and out:
        return Check("envleak", False, ".env has never been committed",
                     "Found in this repo's history: %s\n"
                     "     A key reached this repo's history. Rotate it now and say so in "
                     "the room.\n"
                     "     Rotating is the fix. Deleting the file is not, because the old "
                     "commit still has it." % out.splitlines()[0])
    return Check("envleak", True, ".env is gitignored and never committed")


def check_key_in_files():
    """A key typed into agent.py (or any tracked file) goes out with the next
    canon push. pod_sync.py refuses that push too; this says so earlier."""
    try:
        from support import keyscan
    except Exception as exc:  # noqa: BLE001
        return Check("keyleak", True, "no key in the repo's files (could not check: %s)" % exc,
                     "", "advisory")
    hits = keyscan.tracked(HERE)
    if hits is None:
        return Check("keyleak", True, "no key in the repo's files (skipped, git could not "
                     "list them)", "", "advisory")
    if not hits:
        return Check("keyleak", True, "no key in the repo's files")
    found = "\n".join("       " + keyscan.where(h) for h in hits[:10])
    return Check("keyleak", False, "no key in the repo's files",
                 "A key to Claude is in a file that goes to the team repo:\n%s\n"
                 "     %s\n"
                 "     Open the file and delete the key. The key belongs only in .env, which is\n"
                 "     never pushed. If this key was already pushed, tell your facilitator now."
                 % (found, keyscan.WHY.replace("\n", "\n     ")))


PRACTICE_REPO = "https://github.com/victorsteeb/larkspur-exercise.git"


def check_origin(day0=False):
    """A real reach for the team repo. This is the check that catches the
    corporate proxy that allows browsers and blocks git, which otherwise shows
    up as a dead ten minutes in the first build.

    On Day 0 there is no team repo yet, so the same check runs against the
    practice clone (the public template the team repo is made from): what it
    proves, that git gets out to GitHub from this network, is the same."""
    what = "GitHub" if day0 else "the team repo"
    code, url = _git("remote", "get-url", "origin", timeout=15)
    if code != 0:
        return Check("origin", False, "git can reach %s" % what,
                     "This folder has no 'origin', so it is not a clone.\n"
                     "     Clone it, do not download the zip:\n"
                     "       git clone %s" % (PRACTICE_REPO + " larkspur-check" if day0
                                              else "<your team repo URL>"))
    code, out = _git("ls-remote", "--heads", "origin", timeout=30)
    if code == 0:
        return Check("origin", True, "git can reach %s (%s)" % (what, url))
    text = out.lower()
    if "authentication" in text or "could not read username" in text \
            or "permission denied" in text or "403" in text:
        return Check("origin", False, "git can reach %s" % what,
                     "git got to the remote and was turned away. If the repo is private, "
                     "whoever\n     created it has to add you as a collaborator, and you have "
                     "to accept the invite.\n"
                     "     That is the usual answer, not your SSH key.\n"
                     "     https auth: `gh auth login`.  SSH: `ssh -T git@github.com`.")
    return Check("origin", False, "git can reach %s" % what,
                 "%s\n"
                 "     git could not get to the remote at all. You clone and push on the day, "
                 "so this\n     has to work. In order:\n"
                 "       1. off the corporate VPN, or on guest wifi, try again\n"
                 "       2. behind a proxy? git config --global http.proxy http://your.proxy:port\n"
                 "       3. raise it before the session, not during it"
                 % last_line(out))


def check_vscode():
    """Optional: only people who want to work in VS Code need it. Never blocks READY.
    The `code` command is often not on PATH even when the app is installed, so the
    app itself is looked for too."""
    home = os.path.expanduser("~")
    candidates = [
        "/Applications/Visual Studio Code.app", os.path.join(home, "Applications", "Visual Studio Code.app"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft VS Code", "Code.exe"),
        "/usr/share/code/code", "/snap/bin/code", "/usr/bin/code",
    ]
    found = shutil.which("code") or next((c for c in candidates if c and os.path.exists(c)), None)
    if found:
        return Check("vscode", True, "VS Code found (%s)" % found, "", "advisory")
    return Check("vscode", False, "VS Code not found",
                 "Only needed if you want to work in VS Code. Skip this if you will use a\n"
                 "     terminal or the Claude desktop app. To add it: https://code.visualstudio.com",
                 "advisory")


def check_gh():
    """Only when the GitHub CLI is installed: is it signed in? Optional, because plain git
    with a saved login does the same job. Only the exit code is used, so its output is
    thrown away undecoded (gh prints symbols that a Windows code page cannot decode)."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "auth", "status"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        return Check("gh", False, "GitHub CLI did not answer",
                     "Optional. If you use gh, run: gh auth status", "advisory")
    except OSError:
        ok = False
    if ok:
        return Check("gh", True, "GitHub CLI signed in", "", "advisory")
    return Check("gh", False, "GitHub CLI installed but not signed in",
                 "Optional. If you use gh, run: gh auth login", "advisory")


def check_claude_cli(required=False):
    path = shutil.which("claude")
    if not path:
        return Check("claude", False, "Claude Code installed",
                     "The go-read step and /build both lean on it. Install:\n"
                     "       npm install -g @anthropic-ai/claude-code    (or see docs.claude.com)",
                     "required" if required else "advisory")
    return Check("claude", True, "Claude Code installed (%s)" % path)


def check_mcp_seam():
    """Advisory. The MCP server and client are stdlib-only and need no
    credential, so a broken clone can be caught here in a fifth of a second
    instead of at the end of Build 2. Never blocks READY: nothing before Build
    2 is where it gets used."""
    script = os.path.join(HERE, "support", "mcp_selftest.py")
    cmd = "python3 support/mcp_selftest.py"
    if not os.path.exists(script):
        return Check("mcp", False, "MCP self-test",
                     "support/mcp_selftest.py is missing, so the clone is incomplete.\n"
                     "     Re-clone before Build 2.", "advisory")
    try:
        r = subprocess.run([sys.executable, script], cwd=HERE, capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return Check("mcp", False, "MCP self-test: timed out",
                     "Run it on its own and read the last line:\n       %s" % cmd, "advisory")
    except OSError as exc:
        return Check("mcp", False, "MCP self-test: could not run (%s)" % exc,
                     "Run it on its own and read the last line:\n       %s" % cmd, "advisory")
    verdict = last_line(r.stdout or r.stderr)
    if r.returncode == 0:
        return Check("mcp", True, "MCP self-test: %s" % verdict)
    return Check("mcp", False, "MCP self-test: %s" % verdict,
                 "Nothing before Build 2 needs this, so it is not blocking. For the\n"
                 "     failing line and why:\n       %s" % cmd, "advisory")


def check_credential():
    """Advisory only. The live call below is the verdict. An unset API key does
    NOT mean there is no credential: an `ant auth login` profile is invisible
    here on some setups, and federated credentials always are."""
    try:
        from support.client import credential_status
    except Exception as exc:  # noqa: BLE001
        return Check("credential", True, "credential: could not be read (%s)" % exc,
                     "", "advisory")
    mode, detail = credential_status()
    if mode == "offline":
        return Check("credential", False, "credential: OFFLINE MODE (%s)" % detail,
                     "LARKSPUR_OFFLINE=1 is set, and there is no offline path in this pack.\n"
                     "     Every build calls the real API, so this setting only hides the "
                     "problem\n     until the room is watching. Unset it:\n"
                     "       unset LARKSPUR_OFFLINE            (and take it out of .env)")
    if mode == "empty-key":
        return Check("credential", False, "credential: %s" % detail,
                     "An empty key beats every other credential and fails every request.")
    if mode == "unknown":
        return Check("credential", True, "credential: %s" % detail, "", "advisory")
    if "does not start with sk-ant-" in detail or "AWS_REGION is not set" in detail:
        return Check("credential", False, "credential: %s" % detail,
                     "Check for a stray quote, a trailing space, or a truncated paste.")
    return Check("credential", True, "credential: %s" % detail)


# Where the key is on the build site. The site's own copy uses these words, so a
# participant can match what the terminal says to what the page shows.
SITE_KEY = ('the box "Your key to Claude" in Setting up, Part 3, '
            'or Menu \u2192 Copy your key to Claude')

NO_CREDENTIAL = (
    "No credential reached the API. In the workshop, use 1:\n"
    "       1. Your key from the build site. Copy it from the box \"Your key to Claude\"\n"
    "          in Setting up, Part 3, or Menu \u2192 Copy your key to Claude. Then run this\n"
    "          in your own terminal and paste the key (nothing shows as you paste):\n"
    "            python3 setup.py --key\n"
    "          Windows: Ctrl+V may not paste there. Right-click to paste, or save the key in a\n"
    "          text file outside this folder and run  python setup.py --key-file C:\\path\\key.txt\n"
    "          If the box says the key is not released yet, ask your facilitator.\n"
    "       2. Already sign in to Claude? Use that, no API key needed:\n"
    "            brew install anthropics/tap/ant   (or see docs.claude.com for "
    "Linux/Windows)\n"
    "            ant auth login\n"
    "            ant auth status        # confirms which credential is active\n"
    "       3. Have your own API key? Run  python3 setup.py --key  and paste it\n"
    "       4. On Bedrock? Set the two Bedrock lines in .env (see .env.example)\n"
    "       5. None of these? Ask your facilitator now. Every build calls the API."
)


def check_live_call():
    """The cheapest possible proof that the credential actually works."""
    try:
        from support.client import MissingCredential, get_client
    except Exception as exc:  # noqa: BLE001
        return Check("live", False, "live call to Claude",
                     "support/ did not import (%s: %s). The clone is incomplete, so "
                     "re-clone." % (type(exc).__name__, exc))
    if os.environ.get("LARKSPUR_OFFLINE") == "1":
        return Check("live", False, "live call to Claude",
                     "Skipped, because LARKSPUR_OFFLINE=1 is set. See the credential check "
                     "above:\n     unset it and run again. The live call is the only proof "
                     "that matters here.")
    model = None
    try:
        # The model the agent will run on, so READY proves that one works.
        from support.models import latest
        model = latest("sonnet", "LARKSPUR_MODEL")
        client = get_client()
        response = client.messages.create(
            model=model, max_tokens=8,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        text = "".join(getattr(b, "text", "") for b in response.content).strip()
        return Check("live", True, "live call to Claude (%s): %r" % (model, text or "(empty)"))
    except Exception as exc:  # noqa: BLE001
        try:
            from support.access import explain
            plain = explain(exc)
        except Exception:  # noqa: BLE001: the explainer must never hide the failure
            plain = None
        if plain:
            return Check("live", False, "live call to Claude", plain)
        if isinstance(exc, MissingCredential):
            return Check("live", False, "live call to Claude", str(exc))
        name = type(exc).__name__
        if "resolve authentication" in str(exc).lower() or name == "AuthenticationError":
            hint = NO_CREDENTIAL
        else:
            hint = {
                "APIConnectionError": "Could not reach the API at all. A proxy is probably "
                                      "swallowing it. Try guest wifi or a phone hotspot, and "
                                      "raise it before the day if it stays "
                                      "broken.",
                "PermissionDeniedError": "That credential is valid but has no access to "
                                         "%s." % model,
                "RateLimitError": "Rate limited, because everyone hit the API at once. Wait "
                                  "30s and run this again.",
                "NotFoundError": "Model %s is not available on this credential or region."
                                 % model,
            }.get(name, "Paste this into Claude Code. It has the repo in context.")
        return Check("live", False, "live call to Claude", "%s: %s\n     %s" % (name, exc, hint))


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def run_checks(live=True, day0=False):
    """Dependency order: this laptop, then git, then the credential. The live
    call runs last and only when nothing above it is broken, so a run that is
    going to fail anyway does not spend a token.

    Day 0 (before the session) is the same laptop check with no key and no team
    repo to ask for: GitHub reach is proved against the practice clone, VS Code and
    the GitHub CLI are looked at (never blocking), and there is no live call."""
    checks = [check_python(), check_venv(), check_sdk(), check_writable(),
              check_git_identity(), check_env_ignored(), check_key_in_files(), check_origin(day0),
              check_claude_cli(required=day0)]
    if day0:
        checks += [check_vscode(), check_gh(), check_mcp_seam(),
                   Check("credential", True, "no key needed yet: you copy your key to Claude "
                         "from the build site at the session", "", "advisory")]
        return [c for c in checks if c is not None]
    checks += [check_mcp_seam(), check_credential()]
    # The live call only needs Python and the SDK (an unactivated or missing .venv is
    # advisory and must not skip it, or READY would print with no proof of a credential).
    # A git problem used to
    # hold it back, so a person with both a git fault and no key met the key
    # problem for the first time at gate 1.2. It costs eight tokens: ask now.
    can_call = all(c.ok for c in checks if c.id in ("python", "sdk"))
    if live and can_call:
        checks.append(check_live_call())
    return checks


def do_fix(day0=False):
    venv = os.path.join(HERE, ".venv")
    python = os.path.join(venv, "Scripts" if os.name == "nt" else "bin", "python")
    if not os.path.exists(python):
        print("creating %s ..." % os.path.relpath(venv, HERE))
        result = subprocess.run([sys.executable, "-m", "venv", venv])
        if result.returncode != 0:
            print("\ncouldn't create a venv with %s" % sys.executable)
            print("try: python3 -m pip install --user virtualenv && python3 -m virtualenv .venv")
            return 1
    print("installing dependencies ...")
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    result = subprocess.run([python, "-m", "pip", "install", "-q", "-r",
                             os.path.join(HERE, "requirements.txt")])
    if result.returncode != 0:
        print("\ninstall failed. If you're behind a proxy, try:")
        print("  %s -m pip install --proxy http://your.proxy:port -r requirements.txt" % python)
        return 1
    activate = ".venv\\Scripts\\activate" if os.name == "nt" else "source .venv/bin/activate"
    print("\n%s\nDone. Now run these two lines:\n\n  %s\n  %s\n%s"
          % (RULE, activate, again(), RULE))
    return 0


def _dotenv_path():
    return os.path.join(HERE, ".env")


# A Console API key: classic (sk-ant-api03-), personal (sk-ant-usr-) or service
# account (sk-ant-svc-). Admin keys and sign-in tokens (sk-ant-admin01-,
# sk-ant-oat01-) are refused on purpose. Same rule as the build site's loader.
KEY_SHAPE = re.compile(r"sk-ant-(?:api\d{2}|usr\d{0,2}|svc\d{0,2})-[A-Za-z0-9_\-]{16,}")


def _read_key(key_file=None):
    """The key as typed, pasted or read from a file.

    --key-file: a text file holding only the key (keep it outside the repo and delete
    it afterwards). Windows: getpass reads raw keystrokes there, so Ctrl+V and
    Shift+Insert paste nothing into it. Use a plain prompt instead, where the key
    does show for a moment; a right-click or Ctrl+V works in it the normal way."""
    if key_file:
        with open(os.path.expanduser(key_file), encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    if os.name == "nt":
        print("\nOn Windows the key shows on screen as you paste it. Paste (Ctrl+V or right-click), "
              "press Enter, then clear the window.")
        return input("Paste your key to Claude, then press Enter: ")
    return getpass.getpass("Paste your key to Claude (nothing will show as you paste), then press Enter: ")


def do_key(key_file=None):
    """Save the key to Claude into .env without it ever appearing on screen.

    The facilitator loads one key per cohort on the build site; a participant
    copies it from the box "Your key to Claude" in Setting up, Part 3 (or
    Menu -> Copy your key to Claude) and pastes it here, in their own terminal. getpass draws nothing, the
    key is never echoed or printed (only its last four characters), and nothing
    is written until git confirms .env is ignored, because a key in the repo is
    the one mistake here that follows you home. (With no git on the machine
    there is nothing to commit it with, so that case is let through.) Returns an
    exit code, or None to carry on into the normal checks."""
    code, _ = _git("check-ignore", "-q", ".env")
    if code not in (0, 127):
        print("\nNot saved. .env is not gitignored in this folder, so a key written there "
              "could be pushed.\n  Run  python3 setup.py  first and fix the \u2717 lines "
              "(this is the folder of your team repo clone), then try again.")
        return 1
    try:
        raw = _read_key(key_file)
    except (EOFError, KeyboardInterrupt):
        print("\nNothing saved.")
        return 1
    except OSError as exc:
        print("\nNot saved. Could not read %s (%s)." % (key_file, exc.strerror or exc))
        return 1
    key = raw.strip()
    if key.count("sk-ant-") > 1 or not KEY_SHAPE.fullmatch(key):
        print("\nNot saved. That does not look like a key to Claude (it starts sk-ant-api, "
              "sk-ant-usr or sk-ant-svc, and has no spaces).\n  Copy it again on the build site: " + SITE_KEY + ".\n"
              "  Then run  python3 setup.py --key  again and paste once. An admin key or a "
              "sign-in token is refused on purpose.")
        return 1
    path = _dotenv_path()
    lines, replaced = [], False
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh.read().splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    if not replaced:
                        lines.append("ANTHROPIC_API_KEY=" + key)
                    replaced = True
                else:
                    lines.append(line)
    if not replaced:
        lines.insert(0, "ANTHROPIC_API_KEY=" + key)
    # Written beside it and swapped in, so a crash mid-write never leaves a
    # half-file where the old .env was.
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print("\nSaved to .env (key ending \u2026%s)%s." % (key[-4:], ", replacing the old line"
                                                    if replaced else ""))
    stale = os.environ.get("ANTHROPIC_API_KEY")
    if stale is not None and stale != key:
        print("\n  ! Your shell already has ANTHROPIC_API_KEY set, and that beats .env.\n"
              "    Run  unset ANTHROPIC_API_KEY  and then  " + again() + "  again.")
        return 1
    return None


def main():
    parser = argparse.ArgumentParser(description="Check this seat is ready to build.")
    parser.add_argument("--fix", action="store_true",
                        help="create .venv and install dependencies")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-live", action="store_true", help="skip the live API call")
    parser.add_argument("--day0", action="store_true",
                        help="the laptop check before the session: no key, no team repo yet")
    parser.add_argument("--key-file", metavar="PATH",
                        help="like --key, but read the key from a text file (no pasting into the terminal)")
    parser.add_argument("--key", action="store_true",
                        help="paste your key to Claude from the build site into .env (never shown)")
    args = parser.parse_args()
    global DAY0
    DAY0 = args.day0

    if args.fix:
        return do_fix(day0=args.day0)
    if args.key or args.key_file:
        stopped = do_key(args.key_file)
        if stopped is not None:
            return stopped

    checks = run_checks(live=not args.no_live and not args.day0, day0=args.day0)
    blocking = [c for c in checks if not c.ok and c.severity == "required"]

    if args.json:
        print(json.dumps({
            "mode": "day0" if args.day0 else "full",
            "ready": not blocking,
            "blocking": len(blocking),
            "checks": [{"id": c.id, "ok": c.ok, "label": c.label, "fix": c.fix,
                        "severity": c.severity} for c in checks],
        }, indent=2))
        return 0 if not blocking else 1

    print("\n%s\nLARKSPUR SETUP%s  ·  %s %s\n%s"
          % (RULE, " · DAY 0 CHECK" if args.day0 else "", platform.system(), platform.release(), RULE))
    for c in checks:
        mark = "  ✓" if c.ok else ("  !" if c.severity == "advisory" else "  ✗")
        print("%s %s" % (mark, c.label))
        if c.fix and not c.ok:
            for line in c.fix.splitlines():
                print("      %s" % line)

    print("\n" + RULE)
    if not blocking:
        looks = [c for c in checks if not c.ok and c.severity == "advisory"]
        if args.day0:
            print("READY for the session. Nothing else to do today.")
            if looks:
                print("%d optional item(s) above are worth a look if you plan to use them." % len(looks))
            print("At the start you get your team repo and your key to Claude.")
        elif args.no_live:
            print("READY, apart from the live call to Claude, which you skipped.")
            print("Once you have a key: python3 setup.py --key")
        else:
            print("READY. That is the whole setup.")
            print("Next: python3 run.py K7PQ2M --trace")
        print(RULE)
        return 0
    print("BLOCKED on %d check(s). Fix the ✗ lines above, top to bottom." % len(blocking))
    print("Then: " + again())
    print(RULE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
