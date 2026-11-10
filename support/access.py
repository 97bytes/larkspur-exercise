"""Telling "this laptop cannot reach Claude" apart from "your agent is wrong".

This file is GIVEN. It exists because the two failures used to print the same
thing. A missing, refused or blank key surfaces as a TypeError or a 401 in the
middle of the loop, and every gate that runs the agent then told the person to
go and read their `messages` list. Nothing was wrong with it. The first fault
class is about this seat, so it gets its own tag and its own words.

    explain(exc)   plain-language text for an access failure, or None when the
                   exception is something else (a real bug in the agent)

Every message says what happened, what to type next, and the two or three words
to say to a facilitator. None of them contain a credential: the API's own
sentence is quoted back, with anything key-shaped masked first.
"""

from __future__ import annotations

import re
from typing import Optional

_KEYISH = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
_OUT_OF_CREDIT = re.compile(r"credit balance|usage limit|spend(ing)? limit|billing", re.I)


def _say(exc: BaseException, limit: int = 160) -> str:
    text = _KEYISH.sub("sk-ant-…", str(exc)).strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _chain(exc: BaseException):
    """The exception and the ones it was explicitly raised `from`. Never the
    implicit `__context__`: a real bug raised inside an `except RateLimitError`
    block would otherwise be told it was a busy API, and its traceback lost."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__


def classify(exc: BaseException) -> Optional[str]:
    """The kind of access problem this is, or None."""
    for e in _chain(exc):
        name, text = type(e).__name__, str(e)
        if name == "MissingCredential":
            if "set but empty" in text:
                return "blank_key"
            if "AWS_REGION" in text:
                return "bedrock_region"
            if "isn't installed" in text:
                return "not_installed"
            return "no_credential"
        if name == "TypeError" and "resolve authentication method" in text:
            return "no_credential"
        if name == "AuthenticationError":
            return "key_refused"
        if name == "PermissionDeniedError":
            return "no_model_access"
        if name == "NotFoundError":
            return "no_model_found"
        if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout"):
            return "no_connect"
        # A shared cohort key that hits its spend limit or runs out of credit
        # comes back as a 400, not a 429. Any other 400 is the request itself.
        if getattr(e, "status_code", None) == 400 and _OUT_OF_CREDIT.search(text):
            return "out_of_credit"
        if name in ("RateLimitError", "InternalServerError", "OverloadedError"):
            return "busy"
        if getattr(e, "status_code", None) in (429, 500, 502, 503, 529):
            return "busy"
    return None


_MESSAGES = {
    "no_credential": (
        "[ACCESS] This laptop has no key, token or sign-in to send to Claude.",
        "{what} calls Claude, and it cannot without one. This is not about your code.",
        "Next: copy your key on the build site (the box \"Your key to Claude\" in Setting up, "
        "Part 3, or Menu \u2192 Copy your key to Claude), then run  python3 setup.py --key  in "
        "your own terminal and paste it. Not in a workshop? Run  python3 setup.py  and fix "
        "the first ✗.",
        'Box says the key is not released yet, or still stuck? Tell a facilitator: "no key".'),
    "key_refused": (
        "[ACCESS] Claude refused your key.",
        "Usually a typo, a clipped paste, or a key that has been switched off. This is "
        "not about your code.",
        "Next: in .env make sure the line is  ANTHROPIC_API_KEY=sk-ant-…  with no spaces or "
        "quotes, save, and run this again. Or copy it again from the box \"Your key to Claude\" "
        "on the build site and run  python3 setup.py --key  to paste it afresh.",
        'Still refused? Tell a facilitator: "key refused".'),
    "blank_key": (
        "[ACCESS] Your key is set but blank, and a blank key beats every working sign-in.",
        "This is not about your code.",
        "Next: run  unset ANTHROPIC_API_KEY , delete the empty ANTHROPIC_API_KEY= line in "
        ".env, and run this again.",
        'Still failing? Tell a facilitator: "blank key".'),
    "bedrock_region": (
        "[ACCESS] Your Bedrock token is there, but no region is set.",
        "This is not about your code.",
        "Next: run  export AWS_REGION=us-east-1  (use the region where Claude is switched "
        "on for you), then run this again.",
        'Still failing? Tell a facilitator: "bedrock region".'),
    "no_model_found": (
        "[ACCESS] Claude does not know the model it was asked for, or this key cannot use it.",
        "If you changed the model name in your code, check its spelling first.",
        "Next: if you did not touch it, tell a facilitator and give them the last four "
        "characters of your key. On Bedrock, check the model is switched on in your region.",
        'Say: "model not found".'),
    "no_connect": (
        "[ACCESS] This laptop could not reach Claude.",
        "A VPN, proxy or firewall is the usual cause, not your code and not your key.",
        "Next: try guest wifi or your phone's hotspot, then run this again.",
        'Still failing? Tell a facilitator: "can\'t connect".'),
    "no_model_access": (
        "[ACCESS] Your key works, but it is not allowed to use this model.",
        "This is not about your code.",
        "Next: tell a facilitator and give them the last four characters of your key. On "
        "Bedrock, check the model is switched on in your region.",
        'Say: "no model access".'),
    "busy": (
        "[ACCESS] Claude is busy right now (everyone running at once, or Claude is overloaded).",
        "Nothing is wrong with your agent.",
        "Next: wait 30 seconds and run this again.",
        'Still busy after two tries? Tell a facilitator: "Claude is busy".'),
    "out_of_credit": (
        "[ACCESS] Your key has reached its spending limit, so Claude stopped answering it.",
        "This is not about your code, and everyone on the same key sees it at once.",
        "Next: stop and tell a facilitator now. Running it again will not help until the "
        "limit is raised or a new key is released.",
        'Say: "key out of credit".'),
    "not_installed": (
        "[SETUP] The Claude package is not installed in this Python.",
        "This is not about your code.",
        "Next: run  python3 setup.py --fix , then follow the two lines it prints.",
        'Still stuck? Tell a facilitator: "no package".'),
}


def explain(exc: BaseException, what: str = "This check") -> Optional[str]:
    """Plain-language text for an access failure, or None for anything else.
    `what` names the thing that was running ("This run", "This bench")."""
    kind = classify(exc)
    if kind is None:
        return None
    head, why, nxt, ask = _MESSAGES[kind]
    lines = [head, "  " + why.format(what=what), "  " + nxt, "  " + ask]
    if kind in ("key_refused", "no_model_access", "no_model_found", "no_connect", "busy",
                "out_of_credit"):
        lines.append("  (Claude said: %s)" % _say(exc))
    return "\n".join(lines)
