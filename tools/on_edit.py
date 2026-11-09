#!/usr/bin/python3
"""Claude Code PostToolUse hook: after an Edit/Write/Bash that touches an
applet repo, run the validator and feed any errors back. Wired up in each
applet repo's .claude/settings.json (committed). Silent on success.

Scope: the applet whose repo the edited file is in; else the applet whose
repo the session was opened in ($CLAUDE_PROJECT_DIR); else every applet.

Reads the hook payload from stdin; exit 2 makes Claude Code show stderr to
the model, any other exit code is ignored.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = HERE / "check.py"
GROUP_ROOT = HERE.parents[1]

try:
    payload = json.loads(sys.stdin.read() or "{}")
except ValueError:
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
touched = "\n".join(str(v) for v in (tool_input.get("file_path"), tool_input.get("command")) if v)
if not re.search(r"@inklikeurine|README\.md\b|AGENTS\.md\b|UNLICENSE\b|info\.json\b|CHANGELOG\.md\b|harness\.json\b|tests/", touched):
    sys.exit(0)
if not CHECK.exists():
    sys.exit(0)


def applet_of(path: str):
    """'hush' for any path inside ~/src/cinnamon/hush/, else None."""
    try:
        parts = Path(path).resolve().relative_to(GROUP_ROOT).parts
    except (ValueError, OSError):
        return None
    if parts and not parts[0].startswith(("_", ".")) and parts[0] != HERE.parent.name and (GROUP_ROOT / parts[0] / "files").is_dir():
        return parts[0][len("cinnamon-"):] if parts[0].startswith("cinnamon-") else parts[0]
    return None


scope = applet_of(tool_input.get("file_path") or "") or applet_of(os.environ.get("CLAUDE_PROJECT_DIR") or "")
cmd = [sys.executable, str(CHECK), "--quiet"] + (["--uuid", scope] if scope else [])
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode == 0:
    sys.exit(0)
sys.stderr.write("check.py found errors after editing:\n" + res.stdout + res.stderr)
sys.exit(2)
