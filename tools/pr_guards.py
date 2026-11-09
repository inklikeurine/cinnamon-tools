#!/usr/bin/python3
"""Pull request guards. They only READ the change and never run its code, so
they are safe to run from the base branch's own workflow (guards.yml, on
pull_request_target), where a pull request cannot switch them off. Standard
library only.

    python3 ../cinnamon-tools/tools/pr_guards.py <base-sha>

Run from the applet's folder, with the pull request checked out.
Environment: PR_LABELS (a JSON array of label names), PR_AUTHOR (a login).

Fails when the pull request does one of these without the maintainer's label:
  tests-changed   removes or edits an existing line in tests/ (adding is fine)
  new-access      gives the applet new reach (see NEW ACCESS below)
  rules-changed   changes .github/, .claude/, AGENTS.md or CLAUDE.md: the
                  files that control the checks and the maintainer's AI
                  tools (Dependabot's updates to .github/ are allowed)

NEW ACCESS, for a Cinnamon applet, is anything in the shipped code
(files/<uuid>/) that wasn't there before:
  - a program it runs: the first word given to GLib.spawn_command_line_async,
    the first entry of the list given to Spawn.run, Spawn.LineReader,
    Gio.Subprocess or Python's subprocess, a name passed to
    Spawn.available, or a path under /usr/bin, /bin, /usr/sbin, /sbin or
    /usr/libexec
  - a system service or settings store: a quoted reverse-DNS name such as
    'org.bluez.Device1' or 'org.cinnamon.desktop.background' (D-Bus names
    and interfaces, GSettings schemas)
  - a web address (a host after http:// or https://)
  - a network library: Soup, Gio.SocketClient / SocketService, or Python's
    urllib.request, http.client or socket
  - in metadata.json, a new or changed "role" (the applet replaces a
    built-in part of Cinnamon) or "external-configuration-app" (a program
    Cinnamon runs for the applet's settings)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from lib.applet import is_shipped_path  # noqa: E402

if len(sys.argv) != 2:
    print("usage: pr_guards.py <base-sha>", file=sys.stderr)
    sys.exit(2)
base = sys.argv[1]
labels = set(json.loads(os.environ.get("PR_LABELS") or "[]"))
author = os.environ.get("PR_AUTHOR") or ""


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def try_git(*args: str) -> str | None:
    try:
        return git(*args)
    except RuntimeError:
        return None


def lines(s: str) -> list[str]:
    return [x for x in s.split("\n") if x]


# Compare against where the pull request branched off, so changes that landed
# on the base branch since then don't count against it.
frm = git("merge-base", base, "HEAD").strip()
changes = []
for ln in lines(git("diff", "--no-renames", "--name-status", frm, "HEAD")):
    status, file = ln.split("\t", 1)
    changes.append((status[0], file))
numstat = {}
for ln in lines(git("diff", "--no-renames", "--numstat", frm, "HEAD")):
    added, removed, file = ln.split("\t", 2)
    numstat[file] = (added, removed)

problems = []   # (label, title, items, fix)

# --- tests-changed ----------------------------------------------------------
items = []
for status, file in changes:
    if not file.startswith("tests/"):
        continue
    if status == "D":
        items.append(f"{file}: removed")
    elif status in ("M", "T"):
        n = numstat.get(file)
        if not n or n[1] == "-":
            items.append(f"{file}: changed")
        elif int(n[1]) > 0:
            items.append(f"{file}: {n[1]} existing line(s) removed or edited")
if items:
    problems.append(("tests-changed", "Existing tests were changed", items,
                     "Pull requests may add tests but not edit or remove existing ones, so a test can't be bent to fit broken code. "
                     "If an existing test really is wrong (behaviour changed on purpose), explain why in the pull request; the maintainer "
                     "can allow it with the `tests-changed` label. Don't work around this by leaving the old test and adding a new one "
                     "beside it: tests that are repeated or out of date are removed in a test tidy-up pull request (AGENTS.md, Tests)."))

# --- new-access ---------------------------------------------------------------
SPAWN_CALL = re.compile(
    r"(?:spawn_command_line_async|spawn_command_line_sync|spawn_async\w*|spawnv?|Subprocess\.new|new\s+Gio\.Subprocess|"
    r"Spawn\.run|Spawn\.available|LineReader|subprocess\.(?:run|Popen|call|check_call|check_output))"
    r"\s*\(\s*(?:\{\s*argv\s*:\s*)?\[?\s*(['\"`])([^'\"`]+)\1")
PROGRAM_PATH = re.compile(r"(?<![\w.])(/usr/bin|/bin|/usr/sbin|/sbin|/usr/libexec)/([A-Za-z0-9_.+-]+)")
SERVICE = re.compile(r"(['\"`])((?:org|com|net|io)(?:\.[A-Za-z0-9_-]+){2,})\1")
HOST = re.compile(r"\bhttps?://([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)
NETWORK = [("Soup (web requests)", re.compile(r"\bSoup\b")),
           ("Gio.SocketClient", re.compile(r"\bSocketClient\b")),
           ("Gio.SocketService", re.compile(r"\bSocketService\b")),
           ("Python urllib.request", re.compile(r"\burllib\.request\b|from\s+urllib\s+import\s+request")),
           ("Python http.client", re.compile(r"\bhttp\.client\b")),
           ("Python socket", re.compile(r"^\s*(?:import\s+socket\b|from\s+socket\s+import)", re.M))]


def program_of(arg: str) -> str:
    """'xdg-open %s' -> 'xdg-open'; '/usr/bin/python3' -> 'python3'."""
    first = arg.strip().split()[0] if arg.strip() else ""
    return first.rsplit("/", 1)[-1]


def reach_of(ref: str) -> dict[str, dict[str, str]]:
    """{kind: {thing: first file}} for the shipped code at ref."""
    found = {"program": {}, "service": {}, "host": {}, "network": {}}

    def add(kind: str, thing: str, file: str):
        if thing and thing not in found[kind]:
            found[kind][thing] = file

    for file in lines(git("ls-tree", "-r", "--name-only", ref)):
        if not is_shipped_path(file) or not file.endswith((".js", ".py", ".json", ".sh", ".desktop", ".xml", ".ui")):
            continue
        text = try_git("show", f"{ref}:{file}")
        if text is None or len(text) > 2 * 1024 * 1024:
            continue
        for m in SPAWN_CALL.finditer(text):
            add("program", program_of(m.group(2)), file)
        for m in PROGRAM_PATH.finditer(text):
            add("program", m.group(2), file)
        for m in SERVICE.finditer(text):
            add("service", m.group(2), file)
        for m in HOST.finditer(text):
            add("host", m.group(1).lower(), file)
        for name, rx in NETWORK:
            if rx.search(text):
                add("network", name, file)
    return found


def metadata_at(ref: str | None) -> dict:
    """files/<uuid>/metadata.json at ref (None = the working tree), or {}."""
    for file in lines(git("ls-tree", "-r", "--name-only", ref or "HEAD")):
        if re.fullmatch(r"files/[^/]+/metadata\.json", file):
            text = try_git("show", f"{ref}:{file}") if ref else (open(file, encoding="utf-8").read() if os.path.exists(file) else None)
            try:
                m = json.loads(text) if text else {}
                return m if isinstance(m, dict) else {}
            except ValueError:
                return {}
    return {}


items = []
before, after = reach_of(frm), reach_of("HEAD")
words = {"program": "runs a new program", "service": "uses a new system service or settings store",
         "host": "new web address", "network": "uses a network library for the first time"}
for kind in ("program", "service", "host", "network"):
    for thing, file in sorted(after[kind].items()):
        if thing not in before[kind]:
            items.append(f"{file}: {words[kind]} {thing}")
bm, hm = metadata_at(frm), metadata_at(None)
for key, what in (("role", "replaces a built-in part of Cinnamon"), ("external-configuration-app", "has Cinnamon run a settings program")):
    if hm.get(key) != bm.get(key) and hm.get(key) is not None:
        items.append(f'metadata.json "{key}": {what} ({json.dumps(hm.get(key))}, was {json.dumps(bm.get(key))})')
if items:
    problems.append(("new-access", "New programs, system services, web addresses or network use", items,
                     "New programs the applet runs, system services it talks to, web addresses and network use are where code that "
                     "spies or breaks things would show up, so each needs the maintainer's OK. Explain in the pull request what it's "
                     "for; the maintainer can allow it with the `new-access` label."))

# --- rules-changed ----------------------------------------------------------
def is_rules(f: str) -> bool:
    return f.startswith(".github/") or f.startswith(".claude/") or f in ("AGENTS.md", "CLAUDE.md")


items = [f"{file}: {({'A': 'added', 'D': 'removed'}).get(status, 'changed')}"
         for status, file in changes
         if is_rules(file) and not (author == "dependabot[bot]" and file.startswith(".github/"))]
if items:
    problems.append(("rules-changed", "Files that control the checks or the maintainer's AI tools", items,
                     "These files decide what the checks do (.github/) and what AI tools do with this repository (.claude/, AGENTS.md, "
                     "CLAUDE.md); a change to .claude/settings.json can even run commands on the maintainer's computer. The maintainer "
                     "can allow a change with the `rules-changed` label."))

# --- report -------------------------------------------------------------------
out = []
blocking = [p for p in problems if p[0] not in labels]
for label, title, its, fix in problems:
    allowed = label in labels
    out.append(f"{'ALLOWED' if allowed else 'FAIL'}  {title}" + (f" (the maintainer added `{label}`)" if allowed else ""))
    out.extend(f"  - {i}" for i in its)
    if not allowed:
        out.append(f"  {fix}")
    out.append("")
if not problems:
    out.append("PASS  No existing tests changed, no new programs, services, web addresses or network use, no rule files changed.")
text = "\n".join(out)
print(text)
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write(f"## Pull request guards\n\n```\n{text}\n```\n")
sys.exit(1 if blocking else 0)
