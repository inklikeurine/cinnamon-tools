#!/usr/bin/python3
"""Scaffold a new applet repo from cinnamon-tools/templates: ~/src/cinnamon/<name>/
with the Spices files, its tests/, the repo files (AGENTS.md, the pull
request checks and template) and a fresh `git init`; then run the validator
on it.

    python3 ../cinnamon-tools/tools/new_applet.py <name> "<description>" [--name "Display Name"]
        [--no-settings] [--no-stylesheet] [--shared Spawn,DBusProxy]

With --shared it also declares those canonical cinnamon-tools/shared/
modules in the applet's tests/harness.json and vendors the copies into
files/<uuid>/modules/shared/ (the same as editing harness.json by hand and
running sync_shared.py). The applet body, the README prose, AGENTS.md's
per-applet sections and real tests are still yours to write. Creating the
GitHub repository is not done here: it is public, so the maintainer does
that.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.applet import (  # noqa: E402
    TOOLS, NAME_RE, NEW_APPLET_VERSION, GROUP_ROOT, NOT_APPLETS, UUID_SUFFIX, Applet, placeholder_icon_png, repo_name,
)
from lib.shared import SharedError, canonical_names, sync_applet  # noqa: E402

argv = sys.argv[1:]


def die(msg: str):
    print(f"new_applet: {msg}", file=sys.stderr)
    sys.exit(2)


def take_flag(flag: str, has_value: bool):
    if flag not in argv:
        return None if has_value else False
    i = argv.index(flag)
    if has_value:
        if i + 1 >= len(argv):
            die(f"{flag} needs a value")
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    del argv[i]
    return True


display = take_flag("--name", True)
no_settings = take_flag("--no-settings", False)
no_stylesheet = take_flag("--no-stylesheet", False)
shared_arg = take_flag("--shared", True)
if len(argv) != 2:
    die('usage: new_applet.py <name> "<description>" [--name "Display Name"] [--no-settings] [--no-stylesheet] [--shared A,B]')
shared_names = [x.strip()[:-3] if x.strip().endswith(".js") else x.strip() for x in (shared_arg or "").split(",") if x.strip()]
unknown = [n for n in shared_names if n not in canonical_names()]
if unknown:
    die(f"--shared: no canonical module {', '.join(unknown)} in cinnamon-tools/shared/ (have: {', '.join(canonical_names()) or 'none'})")
name, description = argv
if not NAME_RE.match(name):
    die(f'name "{name}" must be lowercase letters, digits and hyphens')
if name.endswith("-applet") or name == "applet":
    die("drop the 'applet' suffix")
uuid = name + UUID_SUFFIX
dest = GROUP_ROOT / name
if name in NOT_APPLETS or dest.exists():
    die(f"{dest} already exists")
if not description.strip():
    die("description is empty")
if any(ord(c) > 127 for c in description):
    die("description must be ASCII (Spices rejects non-ASCII metadata)")

tpl = TOOLS / "templates"
human = display or " ".join(w.capitalize() for w in name.split("-"))
class_name = "".join(w.capitalize() for w in name.split("-")) + "Applet"
today = date.today().isoformat()


def fill(text: str) -> str:
    return (text.replace("__UUID__", uuid).replace("__NAME__", human).replace("__DESCRIPTION__", description)
            .replace("__CLASS__", class_name).replace("__DATE__", today).replace("__VERSION__", NEW_APPLET_VERSION)
            .replace("__SHORTNAME__", name))


def copy(src: str, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(fill((tpl / src).read_text(encoding="utf-8")), encoding="utf-8")


applet_dir = dest / "files" / uuid
copy("info.json", dest / "info.json")
copy("README.md", dest / "README.md")
copy("CHANGELOG.md", dest / "CHANGELOG.md")
copy("applet.js", applet_dir / "applet.js")
copy("metadata.json", applet_dir / "metadata.json")
if not no_settings:
    copy("settings-schema.json", applet_dir / "settings-schema.json")
if not no_stylesheet:
    copy("stylesheet.css", applet_dir / "stylesheet.css")
(applet_dir / "icon.png").write_bytes(placeholder_icon_png())
# Repo files: AGENTS.md (the rules); the pull request checks and template.
copy("repo/AGENTS.md", dest / "AGENTS.md")
copy("repo/CLAUDE.md", dest / "CLAUDE.md")
copy("repo/gitignore", dest / ".gitignore")
copy("repo/claude-settings.json", dest / ".claude" / "settings.json")
copy("repo/checks.yml", dest / ".github" / "workflows" / "checks.yml")
copy("repo/guards.yml", dest / ".github" / "workflows" / "guards.yml")
copy("repo/pull_request_template.md", dest / ".github" / "pull_request_template.md")
copy("repo/dependabot.yml", dest / ".github" / "dependabot.yml")
(dest / "UNLICENSE").write_text((tpl / "UNLICENSE").read_text(encoding="utf-8"), encoding="utf-8")
try:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True)
except (OSError, subprocess.CalledProcessError) as e:
    print(f"new_applet: warning: git init failed ({e}); run it by hand in {dest}", file=sys.stderr)

copy("smoke.test.js", dest / "tests" / "smoke.test.js")
copy("harness.json", dest / "tests" / "harness.json")
(dest / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)

# Shared modules: declare in harness.json, then vendor the copies.
synced = []
if shared_names:
    hp = dest / "tests" / "harness.json"
    spec = json.loads(hp.read_text(encoding="utf-8"))
    spec["shared"] = shared_names
    hp.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    try:
        synced = sync_applet(Applet(uuid))
    except SharedError as e:
        die(str(e))

print(f"created {dest.name}/ (git repo: info.json, README.md, CHANGELOG.md, files/{uuid}/…, tests/, AGENTS.md, CLAUDE.md, UNLICENSE, .gitignore, .claude/, .github/)")
for rec in synced:
    print(f"shared: {rec['name']} {rec['action']} → {rec['path'].relative_to(GROUP_ROOT)}")
print("next: write the applet, fill the README prose and AGENTS.md's per-applet sections, draw icon.png, add tests, then run the harness and the validator")
print(f"GitHub (the maintainer creates it; it is public): inklikeurine/{repo_name(uuid)}")
res = subprocess.run([sys.executable, str(TOOLS / "tools" / "check.py"), "--uuid", uuid])
sys.exit(res.returncode)
