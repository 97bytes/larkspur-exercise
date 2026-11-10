#!/usr/bin/env python3
"""support/review.py: Larkspur's client review of a team's repository.

You do not run this file. Run `python3 review.py` from the top of the repo.

It reads the team's build and writes FEEDBACK.md: Priya Raghavan's note at the
top, three to five specific observations about the team's own build in the
middle, and her closing ask at the bottom. The note, her four questions and the
closing ask are fixed text, the same in every repository. Only the middle is
written by the model, and it is capped.

WHAT IT READS (everything optional except agent.py): agent.py, scanned
statically and diffed against the version this repository started from, so the
review only credits the team with what the team wrote. PITCH.md and TEAM.md.
readout-trace.json and the evidence block in readout.html. evals/cases.json.
The given files, compared with the versions this repository started from.

It never imports or runs the team's code: it reads files and makes one call to
Claude with your key. The only file it writes is FEEDBACK.md.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import time

import subprocess

# Where the version a repository started from is read. None (in a team's own
# clone) means the repository's first commit, which is the template exactly as
# it was handed out. A directory means that tree instead: the workshop's own
# copy sets it to the pack's exercise/ folder, to review clones from outside.
PACK = None

# None means the newest Sonnet this key can use (support/models.py), looked up
# when a review is made. LARKSPUR_FEEDBACK_MODEL=<id> chooses one yourself.
MODEL_DEFAULT = None
OUT_NAME = "FEEDBACK.md"
GIVEN_TOOL_COUNT = 9          # the nine shipped schemas; anything past this is theirs
MAX_OBSERVATIONS = 5
MIN_OBSERVATIONS = 3
MIDDLE_CHAR_CAP = 5200        # hard cap on the model-written section
AGENT_CHAR_CAP = 24000
OTHER_CHAR_CAP = 6000
DIFF_CHAR_CAP = 14000         # the pod's own edits to agent.py, as a diff
TOTAL_CHAR_CAP = 70000
RULE = "-" * 66

# Files compared with the versions the repository started from. Editing one of
# these is against the house rules in CLAUDE.md, and it is worth knowing before
# a team pitches a number a modified verifier produced. A name missing on either
# side is skipped, so keep this list in step with the files that ship.
GIVEN_FILES = ["verify.py", "eval_harness.py", "bench.py", "readout.py",
               "setup.py", "pod_sync.py", "run.py", "review.py",
               "support/__init__.py", "support/client.py", "support/data.py",
               "support/models.py",
               "support/tools.py", "support/trace.py", "support/offline.py",
               "support/mock_backend.py", "support/mcp_client.py",
               "support/mcp_server.py", "support/mcp_selftest.py",
               "support/access.py", "support/keyscan.py", "support/review.py"]


# ===========================================================================
# The fixed frame. Same in every repo. Priya's voice, no em dashes, and the
# closing ask is verbatim because Block 11's whole instruction is to answer
# it.
# ===========================================================================

PRIYA_PUSH = (
    "Our vendor says we should just be using your best model.\n"
    ">\n"
    "> Why aren't we?"
)
PRIYA_SIG = "Priya Raghavan, Larkspur Airlines"

CLOSING_ASK = ("Before our next meeting, tell me: which model should we be on, and how "
               "will you prove it is the right call?")

FOUR_QUESTIONS = [
    ("What it costs", "Per resolved contact, against the $6.90 a human contact costs us."),
    ("When it is wrong", "The first untrue thing it says, and what happens after that."),
    ("Who runs it", "In June, after you have left."),
    ("What you left out", "The scope you cut, and why."),
]

# The four labels PITCH.md ships under `## Priya asked`, paired with the
# question each one answers. Order matters: it is the order of FOUR_QUESTIONS
# above, so the two tables in the file read as the same four questions asked
# and answered.
PITCH_ANSWER_LABELS = [
    ("Costs", "What it costs"),
    ("Wrong", "When it is wrong"),
    ("Runs it", "Who runs it"),
    ("Left out", "What you left out"),
]

ANSWERS_PREAMBLE = """Four of the lines in your PITCH.md are answers to me rather than to your
verifier, and somebody on your side wrote them between our sessions. I read
those beside the code, not instead of it. Where an answer is carrying a number,
I have said whether the repository backs it."""

ANSWERS_MISSING = """The four lines under `## Priya asked` in your PITCH.md are still empty. They
are one line each and they are not a coding job: cost, what happens when it is
wrong, who runs it in June, and what you left out. Whoever on your side is not
editing agent.py is the right person to write them, and they are the four
things I will ask about first."""

INTRO = """She sent that before this session opened. She means it. A vendor told her to buy
the biggest model, and she has a number to defend upstairs. Her four questions from
day one are still open. Naming a model answers none of them."""

MIDDLE_PREAMBLE = """Larkspur pointed a review agent at your repository. It read the
code. It did not run your agent, and the only file it changed is this one. Each
item below names the file and the line it is about."""

CLOSING_FRAME = """Bring two things. A recommendation, and the measurement behind it. If the model is
not the problem, say so, and bring the number that shows it."""

FOOTER_NOTE = ("Static read only: nothing in this repository was executed, and nothing was "
               "modified except this file. Larkspur Airlines is a fictional training "
               "scenario. Confidential, do not distribute.")


# ===========================================================================
# The model's half
# ===========================================================================

SYSTEM_PROMPT = """You are the Larkspur Airlines client review agent. Larkspur pointed you
at a delivery team's repository for the disruption-care chat agent they
demonstrated on day one. You report to Priya Raghavan, the product manager who
owns it.

Her message to the team before this session, which sits at the top of your report:

    "Our vendor says we should just be using your best model. Why aren't we?"

Write ONLY the middle of that report: three to five observations about THIS
build. The report's opening and its closing ask are written already and are not
yours to repeat.

WHERE THIS SITS. The team spent one day building the agent: the tool schemas,
the agentic loop, and a run across several booking shapes. Everything else is
still ahead of them. Choosing what to optimise, measuring a before and an
after, writing eval cases, and pitching the result all happen today, after they
read this. So an empty PITCH.md, or a repository with no eval cases in
it, is the expected state of play, not a failure to scold them for. What you may say, and should, is what this repository does not yet prove.
That is a different sentence and it is the one Priya needs.

Grounding rules, and they are absolute:

1. Every observation is about code or evidence that is actually in the material
   below. Name the file, and the function, variable, tool name, string, or
   number you are looking at. A reader must be able to open that file and find
   the thing you described.
2. Never invent a file, a function, a metric, or a run. If something important
   is missing, say it is missing. Absence is a finding: no eval cases in the
   repo is an observation, a tool description that is still the placeholder is
   an observation, a loop that never continues is an observation.
3. Do not answer Priya's model question for them and do not recommend a model.
   Your job is to put the evidence in front of them so they have to make the
   case themselves. At least one observation must make her question harder to
   answer with a model name: point at something in their build that a bigger
   model would not fix, or at a number nobody has measured yet.
4. No praise that costs nothing. If something is genuinely good, say what it
   would let them claim in front of a sponsor.
5. Prefer the specific over the important. "Your search_alternatives
   description is 6 characters" beats "tool descriptions matter".
6. MOST OF agent.py IS NOT THEIRS. That file ships with the workshop. Every
   comment in it, every docstring and every one of the six pencil marks that
   label the editable places was written by the workshop and is byte-identical
   in every team's repository. It is scaffold. Never attribute any of it to
   this team.
   Never quote a comment as something they said, flagged, admitted, claimed,
   decided, noticed or contradicted, and never treat a comment as evidence of
   what this team thinks. "Its own comment flags it", "the comment says", and
   "their own comment admits" are all defects: the comment is ours.
   WHAT THIS POD CHANGED is handed to you separately, as a unified diff against
   the shipped template. That diff, PITCH.md, their eval cases, and the run
   evidence are the only places this team has spoken. Ground every observation
   in one of those, or in a computed number from the static scan. The whole
   file is included only so you name real functions, tools and variables
   correctly.
   An empty slot is a fact about what they have not filled in, and the scan
   gives you each one as a number: TONE_ADDENDUM at zero characters, EXTRA_TOOLS
   at zero, no LOCAL_TOOLS executors. You may say a seam is still empty. You may
   not say they wrote a note about it. The pencil marks stay in the file whatever
   they do, so their presence says nothing about progress.

Voice: a reviewer on the client side who has read a lot of these. Direct and
specific. Second person to the team, so "your loop" and "you capped". Priya in
the third person. Short sentences, one point each.

Register rules, and a report that breaks them gets rewritten:
- No paired contrasts. Never "X, not Y", "not X but Y", "X rather than Y",
  "reads clean but". Say the one thing that is true.
- No commentary on your own finding: never "which contradicts", "which
  undercuts", "which matches", "so whether X is Y is unestablished".
- No rhetorical setup before the point. Start each detail with the fact.
- No hedged evaluations ("looks solved", "reads clean", "arguably"). If you
  cannot see it, say you cannot see it.
- Quote a number or a string, then stop. Do not restate it in other words.
- The ask is an instruction with a verb first and a named artifact: "Run
  python3 run.py --all --trace and paste the totals footer." Never "show me",
  never "what would convince me", never a question.
- Only name commands that exist in their repo, with flags that exist:
  python3 run.py <PNR> --trace, python3 run.py --all --trace,
  python3 run.py --show-tools, python3 run.py --tool-tax, python3 verify.py <id>
  (ids 1.2 1.3 1.4 2.1 2.2 3.1 4.1), python3 eval_harness.py (whole suite, no
  per-case flag), python3 bench.py --label <name> --stage 1 --runs 3,
  python3 bench.py --compare <a> <b>, python3 readout.py. Bench files live in
  .workshop/ and never reach the repository, so their absence from the file list
  is expected: ask for the compare output, not for the files.

Formatting rules:

- Plain prose. No markdown headings, no bullet lists, no bold, no code fences.
  Write code names as bare words: agent.py, build_tools(), MAX_TOOL_CALLS.
- NEVER use an em dash or an en dash. Use a comma, a colon, or a full stop.
  This holds inside quotations too: if the string you are quoting contains one,
  put a comma there instead and quote the rest exactly.
- No exclamation marks. Do not open with "Great", "I noticed", or "It's worth
  noting".
- headline: one sentence, under 140 characters, saying what and where.
- detail: two to four sentences, concrete, quoting a number or a string
  whenever you have one.
- ask: one sentence, an instruction: the command to run or the file to paste
  that settles it.

SECOND OUTPUT: answer_notes. If the material below contains a section headed
"THE TEAM'S ANSWERS TO PRIYA'S FOUR QUESTIONS", write one note per answer they
actually wrote, in the order given. Otherwise return an empty array and say
nothing about it.

A note is not a grade out of ten and it is not praise. For each answer:

- verdict is exactly one of: "answered", "thin", "unsupported".
  "answered" means it says something specific and the repository does not
  contradict it. "thin" means it is a sentence with no number, denominator or
  named owner in it, where the question wanted one. "unsupported" means it
  states a number, a rate or a claim that nothing in the material below backs,
  or that the material contradicts. A confident number with no artifact under
  it is unsupported, not thin.
- note is one or two sentences saying why, quoting their own words or the
  number in question. Point at the file that would settle it: bench-after.json,
  evals/cases.json, readout-trace.json, the tool description, the policy table.
  Never rewrite their answer for them.

Judge the answer against the evidence, not against how you would have phrased
it. "We did not measure that" is a good answer and scores "answered"."""

OBS_SCHEMA = {
    "type": "object",
    "properties": {
        # No minItems / maxItems: structured outputs reject both on arrays. The
        # three-to-five range is carried by the system prompt, capped when the
        # observations are rendered, and re-checked after the call.
        "observations": {
            "type": "array",
            "description": "Three to five observations. Never fewer than three, "
                           "never more than five.",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string",
                                 "description": "One sentence, under 140 characters, naming "
                                                "the file and the function or symbol."},
                    "detail": {"type": "string",
                               "description": "Two to four sentences of specifics about what "
                                              "that code actually does."},
                    "ask": {"type": "string",
                            "description": "One sentence: the measurement or artifact that "
                                           "would settle it."},
                },
                "required": ["headline", "detail", "ask"],
                "additionalProperties": False,
            },
        },
        # The between-sessions job for the people not editing agent.py. Empty
        # whenever PITCH.md carries no answers, which is the common case on a
        # first pass: the file then says the lines are empty and who should
        # write them, which is the nudge the pod actually needs.
        "answer_notes": {
            "type": "array",
            "description": "One note per answer the team wrote to Priya's four "
                           "questions, in the order given. Empty array if they "
                           "wrote none.",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string",
                                 "description": "The question, copied exactly from the "
                                                "material: What it costs, When it is "
                                                "wrong, Who runs it, What you left out."},
                    "verdict": {"type": "string", "enum": ["answered", "thin", "unsupported"],
                                "description": "answered, thin, or unsupported."},
                    "note": {"type": "string",
                             "description": "One or two sentences saying why, quoting "
                                            "their words and naming the file that "
                                            "would settle it."},
                },
                "required": ["question", "verdict", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["observations", "answer_notes"],
    "additionalProperties": False,
}

VERDICT_WORDS = {"answered": "Answered", "thin": "Thin",
                 "unsupported": "Not supported by the repository"}


# ===========================================================================
# House rule: no em dashes anywhere, generated or template.
# ===========================================================================

DASHES = "—–―−"


def no_dashes(text: str) -> str:
    """Strip em/en dashes from model output. Instructed, then enforced."""
    if not text:
        return ""
    out = []
    for line in text.split("\n"):
        # A dash used as a list bullet becomes a hyphen bullet, not a comma.
        line = re.sub(r"^(\s*)[%s]\s+" % DASHES, r"\1- ", line)
        # A dash at either end of a line is dropped rather than commafied, so a
        # wrapped sentence never gains a comma the author did not write.
        line = re.sub(r"\s*[%s]+\s*$" % DASHES, "", line)
        line = re.sub(r"^\s*[%s]+\s*" % DASHES, "", line)
        # Everything else becomes a comma, which is what the dash was doing.
        line = re.sub(r"\s*[%s]+\s*" % DASHES, ", ", line)
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"[ \t]+,", ",", text)
    text = re.sub(r",\s*([.;:!?])", r"\1", text)
    return text


def clean_prose(text: str) -> str:
    """Model prose, made safe for a section the script owns the headers of."""
    text = no_dashes((text or "").strip())
    kept = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):          # never let it invent a header
            stripped = stripped.lstrip("#").strip()
        if stripped.startswith("```"):        # never let it open a fence
            continue
        kept.append(stripped)
    text = " ".join(l for l in kept if l)
    return re.sub(r"\s{2,}", " ", text).strip()


def one_sentence(text: str, limit: int) -> str:
    text = clean_prose(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "."


# ===========================================================================
# Reading a build
# ===========================================================================

def read_text(path: str, cap: int = OTHER_CHAR_CAP):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        return None
    if len(body) > cap:
        return body[:cap] + "\n\n[truncated at %d characters for this review]" % cap
    return body


def sha(path: str):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _first_commit(root: str):
    try:
        r = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=root,
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    roots = r.stdout.split() if r.returncode == 0 else []
    return roots[-1] if roots else None


def baseline(root: str, rel: str):
    """The bytes of `rel` as this repository started, or None if unknown."""
    if PACK:
        try:
            with open(os.path.join(PACK, rel), "rb") as fh:
                return fh.read()
        except OSError:
            return None
    first = _first_commit(root)
    if not first:
        return None
    try:
        r = subprocess.run(["git", "show", "%s:%s" % (first, rel)], cwd=root,
                           capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def same_as_baseline(root: str, rel: str):
    """True or False when the started-from version is known, None when it is not."""
    base = baseline(root, rel)
    mine = sha(os.path.join(root, rel))
    if base is None or mine is None:
        return None
    return hashlib.sha256(base).hexdigest() == mine


def _str_len(node):
    """Length of a string literal, including implicit and + concatenation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return len(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _str_len(node.left), _str_len(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan_agent(path: str) -> dict:
    """Static facts about agent.py. Computed, never asserted, never executed."""
    scan = {"error": None, "lines": 0, "functions": [], "tools": [],
            "max_tool_calls": None, "tone_addendum_chars": None,
            "extra_tools": None, "local_tools": [], "model_literal": None,
            "uses_cache_control": False, "uses_thinking": False,
            "uses_stream": False, "loop_present": False, "loop_ends_on_tool_use": None,
            "imports_support": False}
    # Scanned whole, never truncated. AGENT_CHAR_CAP applies to the copy that
    # goes into the prompt; a 30KB agent.py must still get a real scan, and a
    # truncated file would fail to parse and lose every fact below.
    body = read_text(path, cap=10 ** 7)
    if body is None:
        scan["error"] = "could not be read"
        return scan

    scan["lines"] = body.count("\n") + 1
    scan["uses_cache_control"] = "cache_control" in body
    scan["uses_thinking"] = "thinking=" in body or '"thinking"' in body
    scan["uses_stream"] = "messages.stream" in body
    scan["imports_support"] = "from support import" in body or "import support" in body

    try:
        tree = ast.parse(body)
    except SyntaxError as exc:
        scan["error"] = "does not parse (line %s: %s)" % (exc.lineno, exc.msg)
        return scan

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan["functions"].append(node.name)
        if isinstance(node, ast.While):
            scan["loop_present"] = True
            test = ast.dump(node.test)
            if "stop_reason" in test:
                scan["loop_ends_on_tool_use"] = "tool_use" in test

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        value = node.value
        if value is None:
            continue
        for name in names:
            if name == "MAX_TOOL_CALLS" and isinstance(value, ast.Constant):
                scan["max_tool_calls"] = value.value
            elif name == "TONE_ADDENDUM":
                scan["tone_addendum_chars"] = _str_len(value)
            elif name == "EXTRA_TOOLS" and isinstance(value, (ast.List, ast.Tuple)):
                scan["extra_tools"] = len(value.elts)
            elif name == "LOCAL_TOOLS" and isinstance(value, ast.Dict):
                scan["local_tools"] = [_const_str(k) or "?" for k in value.keys]
            elif name == "MODEL":
                scan["model_literal"] = _const_str(value)

    # Tool schemas: every dict literal anywhere in the file that carries a
    # string "name" plus a "description". Survives a rewritten build_tools().
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {_const_str(k): v for k, v in zip(node.keys, node.values)
                if _const_str(k) is not None}
        name = _const_str(keys.get("name")) if "name" in keys else None
        if not name or "description" not in keys or name in seen:
            continue
        seen.add(name)
        scan["tools"].append({"name": name, "desc_chars": _str_len(keys["description"])})

    scan["functions"] = sorted(set(scan["functions"]))
    return scan


def agent_diff(root: str, cap: int = DIFF_CHAR_CAP):
    """The pod's own edits to agent.py, as a unified diff against exercise/agent.py.

    WHY. agent.py is mostly the workshop's own scaffold: the given loop, the
    nine schemas, the pencil marks that label the editable places. Hand a
    reviewer the whole file and it mines OUR comments for findings and reports
    them as the pod's. Live, 9 Sep 2026:
    "search_alternatives description already looks solved but its own comment
    flags it as the second wrong tool" and "MAX_TOOL_CALLS mirrors a number
    picked by a different team". Both cite comments this repository shipped
    with, and a pod asked about either one has no idea what it is being asked.

    The diff is the part of that file the team actually wrote. GIVEN_FILES is
    already hashed against exercise/; this is the same comparison for the one
    file they are supposed to edit.

    Returns (diff_text_or_None, one_line_summary).
    """
    theirs = os.path.join(root, "agent.py")
    shipped = baseline(root, "agent.py")
    if shipped is None:
        return None, ("The shipped agent.py could not be found, so this review could not "
                      "separate their edits from the workshop's scaffold. Treat every "
                      "comment in the file below as the workshop's.")
    try:
        base = shipped.decode("utf-8", errors="replace").splitlines(keepends=True)
        with open(theirs, encoding="utf-8", errors="replace") as fh:
            mine = fh.readlines()
    except OSError as exc:
        return None, "agent.py could not be diffed against the shipped template (%s)." % exc

    lines = list(difflib.unified_diff(
        base, mine, fromfile="agent.py (the shipped template)",
        tofile="agent.py (this pod's)", n=3))
    if not lines:
        return None, ("Their agent.py is byte-identical to the shipped template. Nothing "
                      "in it was written by this team, so every comment, header and "
                      "marker in the file below is the workshop's.")
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    text = "".join(lines).rstrip("\n")
    truncated = ""
    if len(text) > cap:
        # Cut at a hunk boundary so the model never reads half a hunk as whole.
        head = text[:cap]
        at = head.rfind("\n@@")
        text = (head[:at] if at > 0 else head).rstrip()
        truncated = " Diff truncated at %d characters." % cap
    summary = ("%d line(s) added, %d removed.%s" % (added, removed, truncated))
    return text, summary


def read_pitch_answers(root: str) -> list:
    """The four lines under `## Priya asked` in PITCH.md, as
    [(question, label, answer)] for whichever are filled in.

    Same forgiving anchor shape as exercise/verify.py's LEVER_RE and
    STILL_BROKEN_RE, and for the same reason: people write `**Costs:** $0.02`
    and `- Runs it: their CX lead`. Every gap is [ \\t]* rather than \\s*, so
    an empty label does not swallow the next line and read as an answer.
    """
    body = read_text(os.path.join(root, "PITCH.md"), cap=OTHER_CHAR_CAP) or ""
    found = []
    for label, question in PITCH_ANSWER_LABELS:
        pattern = (r"^[ \t>*_-]*\**[ \t]*%s\**[ \t]*:[ \t]*\**[ \t]*(\S.*?)\**[ \t]*$"
                   % label.replace(" ", r"[ _-]?"))
        match = re.search(pattern, body, re.M | re.I)
        if match:
            found.append((question, label, no_dashes(match.group(1).strip())))
    return found


def render_pitch_answers(answers: list) -> str:
    """The answers block for the review prompt. Absent when nobody wrote any,
    so the model is never invited to note an answer that does not exist."""
    if not answers:
        return ""
    L = ["THE TEAM'S ANSWERS TO PRIYA'S FOUR QUESTIONS (PITCH.md, the "
         "`## Priya asked` block)",
         "  Written between the sessions, by hand, and not by whoever edited agent.py.",
         "  Judge each one against the evidence above, in this order."]
    for question, label, answer in answers:
        L.append("")
        L.append("  Q: %s" % question)
        L.append("  A: %s" % answer)
    written = {label for _q, label, _a in answers}
    blank = [q for label, q in PITCH_ANSWER_LABELS if label not in written]
    if blank:
        L.append("")
        L.append("  Still blank, so write no note for these: %s" % ", ".join(blank))
    return "\n".join(L)


def scan_evals(root: str) -> dict:
    out = {"present": False, "cases": None, "hard_gates": None, "graders": [],
           "same_as_example": False, "suites": []}
    path = os.path.join(root, "evals", "cases.json")
    if not os.path.exists(path):
        return out
    out["present"] = True
    example = os.path.join(root, "evals", "cases.example.json")
    if os.path.exists(example) and sha(path) == sha(example):
        out["same_as_example"] = True
    try:
        with open(path, encoding="utf-8") as fh:
            cases = json.load(fh).get("cases") or []
    except (OSError, ValueError):
        out["cases"] = "unparseable"
        return out
    out["cases"] = len(cases)
    out["hard_gates"] = sum(1 for c in cases if c.get("hard_gate"))
    out["suites"] = sorted({c.get("suite") for c in cases if c.get("suite")})
    graders = set()
    for case in cases:
        for spec in (case.get("graders") or [{"type": "judge"}]):
            graders.add(spec.get("type") or "?")
    out["graders"] = sorted(graders)
    return out


def read_evidence(root: str) -> dict:
    """The <script id="evidence"> block readout.py buries in readout.html. It is
    the only banked-gate record that survives a push, because .workshop/ is
    gitignored and never reaches a clone."""
    path = os.path.join(root, "readout.html")
    body = read_text(path, cap=400000)
    if body is None:
        return {}
    match = re.search(r'<script[^>]*id="evidence"[^>]*>(.*?)</script>', body, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1).replace("<\\/", "</"))
    except ValueError:
        return {}


def given_files_report(root: str) -> str:
    """The hash comparison, printed whether or not anything differs.

    render_evidence() only mentions the given files when one of them has been
    edited, which is the right shape for the review prompt and the wrong shape
    for --dry-run: the docstring promises this file hashes the given tree, and a
    dry run that says nothing about it looks like a promise nothing kept. So the
    dry run prints the comparison either way, including which names were
    skipped because they exist on only one side.
    """
    checked, missing, edits = [], [], []
    for rel in GIVEN_FILES:
        same = same_as_baseline(root, rel)
        if same is None:
            missing.append(rel)
            continue
        checked.append(rel)
        if not same:
            edits.append(rel)
    L = ["GIVEN FILES HASHED AGAINST THE VERSIONS THIS REPO STARTED FROM (%d of %d compared)"
         % (len(checked), len(GIVEN_FILES))]
    if edits:
        L.append("  DIFFERS FROM THE SHIPPED PACK: %s" % ", ".join(edits))
        L.append("  exercise/CLAUDE.md says never edit these. A modified verifier or "
                 "harness changes what any number from it means.")
    else:
        L.append("  none differ: every given file in this repo is byte-identical to the pack.")
    if missing:
        L.append("  not compared, absent on one side: %s" % ", ".join(missing))
    return "\n".join(L)


def given_file_edits(root: str) -> list:
    """Given files whose bytes differ from this pack's. exercise/CLAUDE.md says
    never edit these, so a divergence is worth knowing about."""
    edits = []
    for rel in GIVEN_FILES:
        if same_as_baseline(root, rel) is False:
            edits.append(rel)
    return edits


def pod_name(root: str) -> str:
    """The pod's own name from TEAM.md, or the directory name.

    Same parsing shape as exercise/verify.py read_roster(), and it has to be:
    since the ceremony cut of 6 Sep 2026 TEAM.md is `# Team: <name>` (older repos: `# Pod:`) plus one
    `- Name` per person plus a paragraph of instructions, and the old
    heuristic (first short line that is not a bullet) read the last word of
    that paragraph as the pod's name and put it on the To: line.
    An unfilled template still carries its <angle brackets>, anywhere in the
    name (`Room <number>` since 22 Oct 2026, `<your team name>` before)."""
    body = read_text(os.path.join(root, "TEAM.md"), cap=2000) or ""
    for line in body.split("\n"):
        match = re.match(r"#\s*(?:Pod|Team)\s*:\s*(.+)", line.strip(), re.I)
        if not match:
            continue
        name = match.group(1).strip()
        if name and not re.search(r"<[^<>]*>", name) and len(name) < 60:
            return name
    return os.path.basename(os.path.abspath(root))


def read_build(root: str) -> dict:
    """Everything this review is allowed to know about one pod's repo."""
    build = {"root": os.path.abspath(root), "pod": pod_name(root), "files": [],
             "scan": None, "evals": None, "evidence": {}, "given_edits": [],
             "docs": {}, "trace": None, "answers": []}

    agent_path = os.path.join(root, "agent.py")
    build["scan"] = scan_agent(agent_path)
    build["files"].append("agent.py (%d lines)" % build["scan"]["lines"])

    # A doc that is still byte-identical to the shipped template carries no
    # information about this team, and pasting one in invites the reviewer to
    # mine the template's own prompts for findings. Note the state instead.
    #
    # The team's own words come from PITCH.md and from the `author` field on
    # their eval cases (both read below; cases.json is appended to the prompt in full).
    for rel in ("PITCH.md", "TEAM.md"):
        path = os.path.join(root, rel)
        body = read_text(path)
        if body is None:
            continue
        if same_as_baseline(root, rel):
            build["docs"][rel] = ("(byte-identical to the template this repository shipped "
                                  "with. Nothing has been filled in.)")
            build["files"].append("%s (unchanged template)" % rel)
        else:
            build["docs"][rel] = body
            build["files"].append(rel)

    trace_path = os.path.join(root, "readout-trace.json")
    if os.path.exists(trace_path):
        try:
            with open(trace_path, encoding="utf-8") as fh:
                build["trace"] = json.load(fh)
            build["files"].append("readout-trace.json")
        except (OSError, ValueError):
            build["trace"] = None

    build["evidence"] = read_evidence(root)
    if build["evidence"]:
        build["files"].append("readout.html (evidence block)")

    build["evals"] = scan_evals(root)
    if build["evals"]["present"]:
        build["files"].append("evals/cases.json")

    # The between-sessions job, read whether or not PITCH.md is otherwise
    # filled: a pod can answer the four questions and touch nothing else.
    build["answers"] = read_pitch_answers(root)
    if build["answers"]:
        build["files"].append("PITCH.md (%d of 4 answers to Priya)"
                              % len(build["answers"]))

    build["given_edits"] = given_file_edits(root)
    return build


# ===========================================================================
# Packaging it for the model
# ===========================================================================

def render_scan(build: dict) -> str:
    scan = build["scan"]
    L = ["STATIC SCAN OF agent.py (computed from the file, not asserted)"]
    if scan["error"]:
        L.append("  agent.py %s" % scan["error"])
    L.append("  lines: %d" % scan["lines"])
    L.append("  functions defined: %s" % (", ".join(scan["functions"]) or "none"))
    L.append("  MAX_TOOL_CALLS: %s" % scan["max_tool_calls"])
    L.append("  TONE_ADDENDUM: %s characters" % (
        "still empty (0)" if scan["tone_addendum_chars"] in (0, None)
        else scan["tone_addendum_chars"]))
    L.append("  EXTRA_TOOLS declared: %s" % scan["extra_tools"])
    L.append("  LOCAL_TOOLS executors: %s" % (", ".join(scan["local_tools"]) or "none"))
    L.append("  MODEL hard-coded in agent.py: %s" % (scan["model_literal"] or
                                                     "no, imported from support"))
    L.append("  cache_control anywhere: %s" % ("yes" if scan["uses_cache_control"] else "no"))
    L.append("  thinking parameter: %s" % ("yes" if scan["uses_thinking"] else "no"))
    L.append("  streaming: %s" % ("yes" if scan["uses_stream"] else "no"))
    if scan["loop_present"]:
        L.append("  agentic loop: a while loop is present, tests stop_reason == tool_use: %s"
                 % scan["loop_ends_on_tool_use"])
    else:
        L.append("  agentic loop: NO while loop found in the file")

    L.append("  tool schemas found (%d; the pack ships %d):"
             % (len(scan["tools"]), GIVEN_TOOL_COUNT))
    for tool in scan["tools"]:
        chars = tool["desc_chars"]
        flag = ""
        if chars is None:
            flag = "   (built at runtime, length not static)"
        elif chars < 40:
            flag = "   <-- under 40 characters, this is what Claude picks the tool from"
        L.append("    %-22s description %s characters%s"
                 % (tool["name"], "?" if chars is None else chars, flag))
    return "\n".join(L)


def render_evidence(build: dict) -> str:
    L = []
    trace = (build["trace"] or {})
    if trace:
        tokens = trace.get("tokens") or {}
        L.append("LAST COMMITTED WIRE RUN (readout-trace.json, written by readout.py)")
        L.append("  API turns: %s   tool calls: %s" % (trace.get("turns"),
                                                       trace.get("tool_calls")))
        L.append("  tools called, in order: %s"
                 % (", ".join(trace.get("tool_names") or []) or "none"))
        L.append("  tokens: %s in, %s out" % (tokens.get("input"), tokens.get("output")))
        # None is a different fact from 0. None means caching was never switched
        # on; 0 means it was and never hit. Do not collapse them.
        if tokens.get("cache_read") is None and tokens.get("cache_write") is None:
            L.append("  prompt caching: never switched on, no turn carried cache_control")
        else:
            L.append("  prompt caching: %s read, %s written, hit ratio %s"
                     % (tokens.get("cache_read"), tokens.get("cache_write"),
                        trace.get("cache_hit_ratio")))
        L.append("  wall clock: %ss" % trace.get("elapsed"))
        L.append("  thinking seen on the wire: %s" % trace.get("saw_thinking"))
    else:
        L.append("LAST COMMITTED WIRE RUN: none. There is no readout-trace.json in this "
                 "repository, so no run of this agent survived the push.")

    ev = build["evidence"] or {}
    if ev:
        banked = ev.get("banked") or {}
        L.append("")
        L.append("BANKED GATES (evidence block inside readout.html)")
        L.append("  generated: %s   pod: %s   banked by: %s"
                 % (ev.get("generated"), ev.get("pod"), ev.get("banked_by")))
        L.append("  gates banked: %s" % (", ".join(sorted(str(k) for k in banked)) or "none"))

    ev_scan = build["evals"] or {}
    L.append("")
    if not ev_scan.get("present"):
        L.append("EVAL CASES: there is no evals/cases.json in this repository.")
    else:
        L.append("EVAL CASES (evals/cases.json)")
        L.append("  cases: %s   hard gates: %s   graders used: %s"
                 % (ev_scan.get("cases"), ev_scan.get("hard_gates"),
                    ", ".join(ev_scan.get("graders") or []) or "none"))
        L.append("  suites: %s" % (", ".join(ev_scan.get("suites") or []) or "none"))
        if ev_scan.get("same_as_example"):
            L.append("  NOTE: byte-identical to evals/cases.example.json, so the given "
                     "examples were copied and never edited.")

    if build["given_edits"]:
        L.append("")
        L.append("GIVEN FILES THAT DIFFER FROM THE SHIPPED PACK: %s"
                 % ", ".join(build["given_edits"]))
        L.append("  These are given files the team was asked not to edit. A modified "
                 "verifier or harness changes what any number from it means.")
    return "\n".join(L)


def build_user_prompt(build: dict) -> str:
    parts = ["POD: %s" % build["pod"],
             "REPOSITORY: %s" % os.path.basename(build["root"]),
             "FILES THIS REVIEW CAN SEE: %s" % ", ".join(build["files"]),
             "",
             render_scan(build),
             "",
             render_evidence(build),
             ""]

    # What this pod changed, before the file itself, because it is the only part
    # of agent.py they wrote and it is what the observations must cite.
    diff_text, diff_summary = agent_diff(build["root"])
    parts.append("WHAT THIS POD CHANGED IN agent.py")
    if diff_text:
        parts.append("  A unified diff against the shipped template. %s" % diff_summary)
        parts.append("  This is the ONLY part of agent.py this team wrote. Everything else in "
                     "the file below, every comment, every docstring, every pencil mark on "
                     "an editable line, shipped with the repository and is identical in every "
                     "team's copy. Cite this diff, PITCH.md, their eval cases, the run "
                     "evidence or the static scan. Never cite a comment.")
        parts.append("")
        parts.append(diff_text)
    else:
        parts.append("  %s" % diff_summary)
    parts.append("")

    parts.append("=== agent.py, THE WHOLE FILE (workshop scaffold, except the diff above) ===")
    parts.append("Read this only so you name functions, tools and variables correctly. Its "
                 "comments and markers are the workshop's, not this team's, and may not be "
                 "quoted as anything this team said or thinks.")
    parts.append(read_text(os.path.join(build["root"], "agent.py"), AGENT_CHAR_CAP)
                 or "(unreadable)")
    for rel, body in build["docs"].items():
        if rel == "TEAM.md":
            continue
        parts.append("")
        parts.append("=== %s ===" % rel)
        parts.append(body)
    if build["evals"].get("present"):
        cases = read_text(os.path.join(build["root"], "evals", "cases.json"))
        if cases:
            parts.append("")
            parts.append("=== evals/cases.json ===")
            parts.append(cases)
    answers_block = render_pitch_answers(build.get("answers") or [])
    if answers_block:
        parts.append("")
        parts.append(answers_block)
    parts.append("")
    parts.append("Write three to five observations about this build, under the rules you "
                 "were given. Cite the names above. Every observation is grounded in this "
                 "pod's own diff, PITCH.md, their eval cases, the run evidence, or a "
                 "computed number from the static scan. Not in a comment.")
    if answers_block:
        parts.append("Then write one note per answer above, in that order, in "
                     "answer_notes.")
    else:
        parts.append("There are no answers to Priya's four questions in this repository, "
                     "so return an empty answer_notes array and do not mention it in the "
                     "observations.")
    text = "\n".join(parts)
    if len(text) > TOTAL_CHAR_CAP:
        text = text[:TOTAL_CHAR_CAP] + "\n\n[material truncated for this review]"
    return text


# ===========================================================================
# The call
# ===========================================================================

class ReviewFailed(RuntimeError):
    pass


def get_review_client():
    """The exercise's own credential resolver: the same key, and the same
    failure messages, as every other command in the repo."""
    try:
        from .client import MissingCredential, get_client
    except ImportError as exc:
        raise ReviewFailed("Could not import support/client.py (%s). The clone is "
                           "incomplete, so re-clone." % exc)
    if os.environ.get("LARKSPUR_OFFLINE") == "1":
        raise ReviewFailed(
            "LARKSPUR_OFFLINE=1 is set, and there is no offline mode for this review.\n"
            "  Fix: unset LARKSPUR_OFFLINE   (and take it out of .env)")
    try:
        return get_client()
    except MissingCredential as exc:
        raise ReviewFailed(str(exc))


def ask_for_observations(client, build: dict, model: str) -> list:
    prompt = build_user_prompt(build)
    try:
        # thinking is disabled here on purpose: with the model's default adaptive
        # thinking, a full pod repo makes the review think past max_tokens and the
        # response comes back as ONE thinking block, zero text — stop_reason
        # max_tokens, nothing to parse (first live-repo run, Sep 3 2026). A
        # structured-output review doesn't need it; the budget buys observations.
        kwargs = dict(
            model=model, max_tokens=4000, system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": OBS_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        # Newer models refuse {"type": "disabled"} and say to send
        # {"type": "between_tools"} instead (claude-sonnet-5-5, Nov 9 2026); older
        # ones only know "disabled". Try one, and switch on that exact 400.
        try:
            response = client.messages.create(thinking={"type": "disabled"}, **kwargs)
        except Exception as first:  # noqa: BLE001
            if "between_tools" not in str(first):
                raise
            response = client.messages.create(thinking={"type": "between_tools"}, **kwargs)
    except Exception as exc:  # noqa: BLE001, one repo's failure is not the batch's
        raise ReviewFailed(credential_hint(exc))

    text = "".join(b.text for b in response.content
                   if getattr(b, "type", None) == "text")
    try:
        payload = json.loads(text)
    except ValueError:
        raise ReviewFailed("the review agent did not return parseable JSON")

    observations, dropped = [], 0
    for item in (payload.get("observations") or [])[:MAX_OBSERVATIONS]:
        headline = one_sentence(item.get("headline", ""), 160)
        detail = clean_prose(item.get("detail", ""))
        ask = one_sentence(item.get("ask", ""), 240)
        if not headline or not detail:
            continue
        # Belt and braces: FEEDBACK.md goes into the team's repository, and
        # nothing in it may point at the workshop's own files.
        joined = " ".join([headline, detail, ask]).lower()
        if "facilitator/" in joined or "facilitator\\" in joined:
            dropped += 1
            continue
        observations.append({"headline": headline, "detail": detail, "ask": ask})
    if len(observations) < MIN_OBSERVATIONS:
        raise ReviewFailed(
            "only %d usable observation(s) came back, wanted at least %d%s. Re-run "
            "this repo on its own." % (len(observations), MIN_OBSERVATIONS,
                                       "" if not dropped else
                                       " (%d dropped for naming the facilitator tree)"
                                       % dropped))

    # The notes on their four answers. Capped at four, and never more than the
    # number of answers they actually wrote: a note about an answer that is not
    # in the file is the one thing that would cost the room.
    wrote = [q for q, _label, _a in (build.get("answers") or [])]
    notes = []
    for item in (payload.get("answer_notes") or [])[:len(wrote)]:
        question = clean_prose(item.get("question", ""))
        verdict = (item.get("verdict") or "").strip().lower()
        note = clean_prose(item.get("note", ""))
        if verdict not in VERDICT_WORDS or not note:
            continue
        # Snap the question back onto one of theirs. A model that paraphrased
        # the label still lands in the right row, and one that invented a
        # question is dropped.
        match = next((q for q in wrote if q.lower() in question.lower()
                      or question.lower() in q.lower()), None)
        if match is None:
            continue
        notes.append({"question": match, "verdict": verdict,
                      "note": one_sentence(note, 420)})
    return observations, notes


def credential_hint(exc: Exception) -> str:
    """The same plain [ACCESS] words every other command prints; the raw
    exception only when it is not an access problem."""
    try:
        from support.access import explain
        plain = explain(exc, "The review")
    except Exception:  # noqa: BLE001: the explainer must never hide the failure
        plain = None
    if plain:
        return plain
    return "%s: %s" % (type(exc).__name__, exc)


# ===========================================================================
# Rendering FEEDBACK.md
# ===========================================================================

def render_answers_section(build: dict, notes: list) -> list:
    """`## Your four answers`, between the observations and the closing ask.

    Present in every file, filled or not. Empty is the finding that matters
    most here: it says out loud that four lines nobody has to code are still
    blank, and who should write them.
    """
    answers = build.get("answers") or []
    L = ["## Your four answers", ""]
    if not answers:
        L.append(ANSWERS_MISSING)
        L.append("")
        return L
    L.append(ANSWERS_PREAMBLE)
    L.append("")
    by_question = {n["question"]: n for n in notes}
    L.append("| My question | Your answer | My read |")
    L.append("| --- | --- | --- |")
    for question, _label, answer in answers:
        note = by_question.get(question)
        if note:
            read = "**%s.** %s" % (VERDICT_WORDS[note["verdict"]], note["note"])
        else:
            read = "Read, no note."
        # A pipe inside an answer would break the row, and somebody will type
        # one: `Costs: $0.02 | model only`.
        L.append("| **%s** | %s | %s |"
                 % (question, answer.replace("|", "/"), read.replace("|", "/")))
    blank = [q for label, q in PITCH_ANSWER_LABELS
             if q not in {a[0] for a in answers}]
    L.append("")
    if blank:
        L.append("Still blank in your PITCH.md: %s. One line each."
                 % ", ".join(blank))
    else:
        L.append("All four answered. Bring the artifact behind each one to our next "
                 "meeting, not the sentence.")
    L.append("")
    return L


def render_feedback(build: dict, observations: list, model: str, notes=None) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M")
    L = ["# Client review: Larkspur disruption-care agent", ""]
    L.append("**To:** %s  " % build["pod"])
    L.append("**From:** Larkspur client review agent, on behalf of Priya Raghavan  ")
    L.append("**Re:** the disruption-care agent you walked us through in our last session  ")
    L.append("**Generated:** %s" % stamp)
    L += ["", "## Priya's note", ""]
    L.append("> %s" % PRIYA_PUSH)
    L.append(">")
    L.append("> %s" % PRIYA_SIG)
    L += ["", INTRO, ""]

    L += ["## Still open from day one", ""]
    L.append("| Her question | What she means by it |")
    L.append("| --- | --- |")
    for question, meaning in FOUR_QUESTIONS:
        L.append("| **%s** | %s |" % (question, meaning))
    L.append("")

    L += ["## What the review agent found", "", MIDDLE_PREAMBLE, ""]
    middle = []
    for i, obs in enumerate(observations, start=1):
        middle.append("**%d. %s**" % (i, obs["headline"]))
        middle.append("")
        middle.append(obs["detail"])
        if obs["ask"]:
            middle.append("")
            middle.append(obs["ask"])
        middle.append("")
    block = "\n".join(middle).rstrip()
    if len(block) > MIDDLE_CHAR_CAP:
        block = block[:MIDDLE_CHAR_CAP].rsplit("\n\n", 1)[0].rstrip()
    L.append(block)

    L += [""] + render_answers_section(build, notes or [])

    L += ["## Before our next meeting", ""]
    L.append("> %s" % CLOSING_ASK)
    L.append(">")
    L.append("> %s" % PRIYA_SIG)
    L += ["", CLOSING_FRAME, ""]

    L += ["## What this review read", ""]
    for item in build["files"]:
        L.append("- `%s`" % item)
    if build["given_edits"]:
        L.append("- given files that differ from the shipped pack: %s"
                 % ", ".join("`%s`" % f for f in build["given_edits"]))
    L.append("")
    L.append("Reviewer: `%s`. %s" % (model, FOOTER_NOTE))
    L.append("")
    return no_dashes("\n".join(L))


# ===========================================================================
# Driving it
# ===========================================================================

def is_participant_repo(path: str) -> bool:
    return os.path.isdir(path) and os.path.exists(os.path.join(path, "agent.py"))


def review_repo(path: str, model: str = MODEL_DEFAULT, dry_run: bool = False,
                client=None, to_stdout: bool = False, skip_existing: bool = False) -> dict:
    """Review ONE repo. The one entry point: review.py and the workshop's
    batch tools both call this.

    Everything main() does per repo goes through here, so a caller that imports
    this module gets the same review the CLI gives and there is one code path to
    keep honest. Returns a dict rather than a tuple because callers need the
    counts, not just the sentence:

        {ok, message, observations, answers_graded, wrote, pod, path}

    client is optional: pass one to reuse a connection across a cohort, or leave
    it None and this resolves its own. dry_run makes no API call and writes
    nothing; it fills in `message` with the scan summary instead.
    """
    if not model:
        from .models import latest
        model = latest("sonnet", "LARKSPUR_FEEDBACK_MODEL")
    root = os.path.abspath(path)
    out = {"ok": False, "message": "", "observations": 0, "answers_graded": 0,
           "wrote": False, "pod": os.path.basename(root), "path": root}
    if not is_participant_repo(root):
        out["message"] = "no agent.py at %s" % root
        return out

    out_path = os.path.join(root, OUT_NAME)
    if skip_existing and os.path.exists(out_path):
        out.update(ok=True, message="skipped, %s already exists" % OUT_NAME)
        return out

    build = read_build(root)
    out["pod"] = build["pod"]
    # A syntax error is a finding, not a reason to skip the repo: the scan says
    # so, the model sees it, and the pod still gets a file. Only an unreadable
    # agent.py stops the review.
    if build["scan"]["error"] == "could not be read":
        out["message"] = "agent.py could not be read"
        return out

    if dry_run:
        answers = build.get("answers") or []
        prompt = build_user_prompt(build)
        out.update(ok=True, answers_graded=len(answers),
                   message="dry run, no API call: %d character prompt, %d of 4 answers "
                           "in PITCH.md" % (len(prompt), len(answers)))
        return out

    if client is None:
        try:
            client = get_review_client()
        except ReviewFailed as exc:
            out["message"] = str(exc)
            return out
    try:
        observations, notes = ask_for_observations(client, build, model)
    except ReviewFailed as exc:
        out["message"] = str(exc)
        return out

    page = render_feedback(build, observations, model, notes)
    out.update(observations=len(observations), answers_graded=len(notes))
    suffix = "" if not notes else ", %d of 4 answers graded" % len(notes)
    if to_stdout:
        print(page)
        out.update(ok=True, message="%d observations%s, printed to stdout"
                                    % (len(observations), suffix))
        return out
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(page)
    except OSError as exc:
        out["message"] = "could not write %s: %s" % (OUT_NAME, exc)
        return out
    out.update(ok=True, wrote=True,
               message="%d observations%s, wrote %s" % (len(observations), suffix, OUT_NAME))
    return out
