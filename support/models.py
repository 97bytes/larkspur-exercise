"""Which Claude model to use: the newest one this key can reach, never a pinned id.

This file is GIVEN. Model ids change faster than a workshop kit does, and not
every key can use every model, so nothing in the pack names one. Each tier the
pack needs (sonnet for the agent and the review, opus for the eval judge) is
looked up once from the API's own list of models this credential can use, and
the newest in that tier wins.

    latest("sonnet")   the newest Sonnet this key can use
    latest("opus")     the newest Opus

The lookup is saved in .workshop/models.json for the day, per credential, so a
run of verify.py does not ask again, and a bench 'before' and 'after' on the
same day run on the same model. A different key on the same laptop asks afresh.

When the list cannot be read (no credential yet, no network, or Bedrock, which
does not offer it), the tier's fallback id below is used, so nothing stops here:
the live call in setup.py is still the one verdict on whether the model works.

To choose a model yourself, set an environment variable and it wins over the
lookup: LARKSPUR_MODEL (the agent), LARKSPUR_JUDGE_MODEL (the eval judge),
LARKSPUR_FEEDBACK_MODEL (the client review).
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(_ROOT, ".workshop", "models.json")

# Used only when the API's model list cannot be read. Not a pin: a key that can
# list models never uses these.
FALLBACK = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-4-8",
            "haiku": "claude-haiku-4-5"}

_RESOLVED: dict = {}


def _who() -> str:
    """Which credential the cache belongs to, without the credential itself."""
    from .client import credential_status
    mode, detail = credential_status()
    return "%s|%s" % (mode, detail)


def _read_cache(who: str) -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return {}
    if saved.get("day") != time.strftime("%Y-%m-%d") or saved.get("who") != who:
        return {}
    return saved.get("models") or {}


def _write_cache(who: str, models: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump({"day": time.strftime("%Y-%m-%d"), "who": who, "models": models},
                      fh, indent=2)
    except OSError:
        pass


def newest(ids_with_dates, family: str) -> Optional[str]:
    """The newest id in a family from (id, created_at) pairs. An id belongs to
    a family when the family is one of its dash-separated words, so
    claude-sonnet-5 and claude-3-5-sonnet-20241022 are both sonnet."""
    found = [(created, mid) for mid, created in ids_with_dates
             if family in str(mid).lower().split("-")]
    if not found:
        return None
    return max(found, key=lambda pair: (str(pair[0]), pair[1]))[1]


def _list_models():
    """Every (id, created_at) this credential can see. Raises when it cannot."""
    from .client import get_client
    client = get_client()
    if type(client).__name__ != "Anthropic":
        raise RuntimeError("this credential does not list models")
    client = client.with_options(max_retries=0, timeout=8.0)
    return [(m.id, getattr(m, "created_at", "")) for m in client.models.list(limit=100)]


def latest(family: str, env: Optional[str] = None) -> str:
    """The model id for a tier: the env override, else the newest this key can
    use, else the tier's fallback."""
    if env and os.environ.get(env, "").strip():
        return os.environ[env].strip()
    if family in _RESOLVED:
        return _RESOLVED[family]
    try:
        who = _who()
    except Exception:  # noqa: BLE001: choosing a model must never stop a run
        who = "unknown"
    models = _read_cache(who)
    if family not in models:
        try:
            listed = _list_models()
        except Exception:  # noqa: BLE001
            listed = None
        if listed:
            models = {fam: newest(listed, fam) for fam in FALLBACK}
            models = {fam: mid for fam, mid in models.items() if mid}
            _write_cache(who, models)
    _RESOLVED[family] = models.get(family) or FALLBACK[family]
    return _RESOLVED[family]
