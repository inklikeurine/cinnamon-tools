#!/usr/bin/python3
"""Build one applet for release, into cinnamon-tools/out/<uuid>/ (ignored by git).

    python3 ../cinnamon-tools/tools/package.py <uuid> [--spices] [--skip-tests]

Without --spices: a zip, <uuid>-<version>.zip, whose single top-level entry
is files/<uuid>/, so it unpacks straight into ~/.local/share/cinnamon/applets/.

With --spices: the folder to copy into a fork of linuxmint/cinnamon-spices-applets,
out/<uuid>/spices/<uuid>/, built from exactly the five entries Spices takes:
info.json, README.md, CHANGELOG.md, screenshot.png and files/. Nothing else
from the repo goes in: not tests/, not .github/, .claude/ or any other repo
file.

Either way it refuses to build while the validator reports any error or any
Spices warning (--spices), or while the headless tests fail.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.applet import GROUP_ROOT, SPICES_ENTRIES, TOOLS, find_applet, tree_files  # noqa: E402

argv = sys.argv[1:]
skip_tests = "--skip-tests" in argv
spices = "--spices" in argv
argv = [a for a in argv if a not in ("--skip-tests", "--spices")]
if len(argv) != 1:
    print("usage: package.py <uuid> [--spices] [--skip-tests]", file=sys.stderr)
    sys.exit(2)
a = find_applet(argv[0])
if not a or not a.applet_dir.is_dir():
    print(f"package: no applet {argv[0]} with files/<uuid>/ beside cinnamon-tools", file=sys.stderr)
    sys.exit(2)

print("== validator (Spices rules promoted to errors)")
res = subprocess.run([sys.executable, str(TOOLS / "tools" / "check.py"), "--spices", "--uuid", a.uuid])
if res.returncode != 0:
    print("package: fix the findings above first", file=sys.stderr)
    sys.exit(1)
if not skip_tests:
    print("== headless tests")
    res = subprocess.run([sys.executable, str(TOOLS / "harness" / "run.py"), a.uuid, "--tests"])
    if res.returncode != 0:
        print("package: tests failed", file=sys.stderr)
        sys.exit(1)

version = a.version() or "0.0.0"
a.out_dir.mkdir(parents=True, exist_ok=True)

if spices:
    dest = a.out_dir / "spices" / a.uuid
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for entry in SPICES_ENTRIES:
        src = a.dir / entry
        if src.is_dir():
            for p in tree_files(src):
                t = dest / entry / p.relative_to(src)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, t)
        elif src.exists():
            shutil.copy2(src, dest / entry)
    print(f"built {dest.relative_to(GROUP_ROOT)}/ for the Spices fork (v{version})")
    for p in tree_files(dest):
        print("  " + str(p.relative_to(dest.parent)))
    sys.exit(0)

out = a.out_dir / f"{a.uuid}-{version}.zip"
if out.exists():
    out.unlink()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in tree_files(a.applet_dir):
        z.write(p, f"{a.uuid}/{p.relative_to(a.applet_dir)}")
print(f"built {out.relative_to(GROUP_ROOT)}")
with zipfile.ZipFile(out) as z:
    for n in z.namelist():
        print("  " + n)
