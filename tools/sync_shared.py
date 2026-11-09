#!/usr/bin/python3
"""Copy canonical shared modules (cinnamon-tools/shared/<Name>.js) into the applets
that declare them. Standard library only.

    python3 ../cinnamon-tools/tools/sync_shared.py [<uuid>...] [--check] [--list]

  (no uuid)   every applet in the repo
  --check     report what would change and exit 1 if anything is out of
              date, without writing
  --list      print the canonical modules and which applets embed each

An applet declares its modules in <applet>/tests/harness.json:
    "shared": ["Spawn", "DBusProxy"]
and receives verbatim copies at files/<uuid>/modules/shared/<Name>.js,
each headed by '// @shared <Name>.js <sha256>' so check.py can warn when a
copy drifts from the canonical file. Never edit a copy: fix
cinnamon-tools/shared/<Name>.js and run this again. Files in modules/shared/ that
are not declared are reported, never deleted.

Exit 0 = nothing to do / everything written, 1 = --check found work or a
declared module has no canonical file, 2 = usage.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.applet import GROUP_ROOT, Applet, discover_applets  # noqa: E402
from lib.shared import SharedError, canonical_names, declared_modules, stray_copies, sync_applet  # noqa: E402

args = sys.argv[1:]
check_only = "--check" in args
list_only = "--list" in args
uuids = [a for a in args if not a.startswith("--")]
for a in args:
    if a.startswith("--") and a not in ("--check", "--list"):
        print(f"sync_shared.py: unknown option {a}", file=sys.stderr)
        sys.exit(2)

applets = discover_applets()
if uuids:
    known = {a.uuid for a in applets}
    for u in uuids:
        if u not in known:
            print(f"sync_shared.py: no applet folder named {u}", file=sys.stderr)
            sys.exit(2)
    applets = [a for a in applets if a.uuid in uuids]


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(GROUP_ROOT))
    except ValueError:
        return str(p)


if list_only:
    users: dict[str, list[str]] = {n: [] for n in canonical_names()}
    for a in applets:
        try:
            for n in declared_modules(a):
                users.setdefault(n, []).append(a.uuid)
        except SharedError as e:
            print(f"  ! {e}")
    if not users:
        print("no canonical modules in cinnamon-tools/shared/")
    for n, us in sorted(users.items()):
        tag = "" if n in canonical_names() else "  (NO CANONICAL FILE)"
        print(f"{n}.js{tag}: " + (", ".join(us) if us else "(unused)"))
    sys.exit(0)

problems = 0
work = 0
for a in applets:
    try:
        records = sync_applet(a, write=not check_only)
    except SharedError as e:
        print(f"{a.uuid}: {e}")
        problems += 1
        continue
    for r in records:
        if r["action"] != "unchanged":
            work += 1
        print(f"{rel(r['path'])}: {r['action']}")
    for p in stray_copies(a):
        print(f"{rel(p)}: not declared in {rel(a.spec_path)} 'shared' — add it there or remove the file")
        problems += 1

if not applets:
    print("no applets found")
summary = f"{len(applets)} applet(s), {work} file(s) " + ("to update" if check_only else "written") + f", {problems} problem(s)"
print(summary)
sys.exit(1 if problems or (check_only and work) else 0)
