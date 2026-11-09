#!/usr/bin/python3
""""Your new tests must fail without your change": runs a pull request's new
or changed test suites against the base branch's code, and lists each new or
changed test with how it did there. At least one must fail there; if none
do, they don't test the change. Those that pass there are named too, so the
pull request can say why each is needed. Standard library only.

    python3 ../cinnamon-tools/tools/fail_first.py <name> <base-sha>

Run from the applet's folder, with the pull request checked out.
Environment: PR_LABELS (a JSON array of label names).

This runs the pull request's test code, so it belongs in checks.yml (on
pull_request, with no secrets), never in guards.yml.

Nothing to prove, so it passes, when:
  - no shipped file changed (anything outside files/<uuid>/: README,
    CHANGELOG, screenshot, tests or repo files)
  - the only shipped change is metadata.json's version (a release)
  - the maintainer added the `no-test-needed` label (e.g. a clean-up that
    changes no behaviour)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.applet import GROUP_ROOT, PYTHON, TOOLS, find_applet, is_shipped_path  # noqa: E402

SUITE_TIMEOUT = 180
if len(sys.argv) != 3:
    print("usage: fail_first.py <name> <base-sha>", file=sys.stderr)
    sys.exit(2)
name, base = sys.argv[1:]
labels = set(json.loads(os.environ.get("PR_LABELS") or "[]"))
app = find_applet(name)
if not app:
    print(f"fail_first: no applet named {name} in {GROUP_ROOT}", file=sys.stderr)
    sys.exit(2)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=app.dir, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def try_git(*args: str) -> str | None:
    try:
        return git(*args)
    except RuntimeError:
        return None


def report(text: str, code: int):
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## New tests fail without the change\n\n```\n{text}\n```\n")
    sys.exit(code)


frm = git("merge-base", base, "HEAD").strip()
changes = []
for ln in git("diff", "--no-renames", "--name-status", frm, "HEAD").splitlines():
    if ln:
        status, file = ln.split("\t", 1)
        changes.append((status[0], file))

shipped = [(s, f) for s, f in changes if is_shipped_path(f)]
if not shipped:
    report("PASS  No shipped code changed (nothing under files/), so there is no change for a test to prove.", 0)

meta_rel = f"files/{app.uuid}/metadata.json"
if len(shipped) == 1 and shipped[0] == ("M", meta_rel):
    try:
        before = json.loads(git("show", f"{frm}:{meta_rel}"))
        after = json.loads((app.dir / meta_rel).read_text(encoding="utf-8"))
        before.pop("version", None)
        after.pop("version", None)
        if before == after:
            report("PASS  Only the version in metadata.json changed (a release), so there is nothing for a test to prove.", 0)
    except (RuntimeError, ValueError, OSError):
        pass   # fall through to the real check

if "no-test-needed" in labels:
    report("ALLOWED  The maintainer added `no-test-needed`, so this change doesn't need a test that fails without it.", 0)

suites = [Path(f).name for s, f in changes
          if s in ("A", "M") and re.fullmatch(r"tests/[^/]+\.test\.(js|py)", f)]
if not suites:
    report("\n".join([
        "FAIL  This pull request changes shipped code but adds or changes no test suite (tests/*.test.js or tests/*.test.py).",
        "  Add a test that fails without your change. If the change really can't have one (a clean-up that changes no behaviour, say),",
        "  explain why in the pull request; the maintainer can allow it with the `no-test-needed` label.",
    ]), 1)

START = re.compile(r"^T\.(run|runAsync|skip)\(")


def blocks_of(src: str) -> list[str]:
    """One block per test: from a line that starts T.run(, T.runAsync( or
    T.skip( to the next. A block whose text is not in the base branch's copy
    of the suite is a new or changed test. Tests made in a loop don't split
    this way, and Python suites aren't split at all; such a suite is judged
    as a whole."""
    ls = [ln for ln in src.split("\n") if not re.fullmatch(r"\s*(imports\.system\.exit\(T\.summary\(\)\)|T\.exit\(\));?\s*", ln)]
    starts = [i for i, ln in enumerate(ls) if START.match(ln)]
    return ["\n".join(ls[st:(starts[k + 1] if k + 1 < len(starts) else len(ls))]).strip() for k, st in enumerate(starts)]


# The base branch's code, with this pull request's tests/ dropped in.
tmp = Path(tempfile.mkdtemp(prefix=f"fail-first-{app.uuid}-"))
results = []
try:
    tar = subprocess.run(["git", "archive", "--format=tar", frm], cwd=app.dir, capture_output=True)
    if tar.returncode != 0:
        raise RuntimeError(f"git archive failed: {tar.stderr.decode(errors='replace')}")
    x = subprocess.run(["tar", "-x", "-C", str(tmp)], input=tar.stdout, capture_output=True)
    if x.returncode != 0:
        raise RuntimeError(f"tar failed: {x.stderr.decode(errors='replace')}")
    shutil.rmtree(tmp / "tests", ignore_errors=True)
    shutil.copytree(app.tests_dir, tmp / "tests")
    applet_dir, tests_dir, harness_dir = tmp / "files" / app.uuid, tmp / "tests", TOOLS / "harness"
    for s in suites:
        suite = tests_dir / s
        results_file = tmp / f".results-{s}.json"
        if s.endswith(".test.js"):
            cmd = ["cjs", str(suite), str(applet_dir), str(harness_dir), str(tests_dir)]
        else:
            cmd = [PYTHON, "-B", str(suite), str(applet_dir), str(tests_dir)]
        try:
            r = subprocess.run(cmd, cwd=GROUP_ROOT, capture_output=True, text=True, timeout=SUITE_TIMEOUT,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "HARNESS_RESULTS": str(results_file)})
            code, out = r.returncode, r.stdout
        except subprocess.TimeoutExpired:
            code, out = 124, ""
        m = re.search(r"^(PASSED|FAILED):.*$", out, re.M)
        summary = m.group(0) if m else f"exit {code}"
        ran = None
        if results_file.exists():
            try:
                ran = json.loads(results_file.read_text())
            except ValueError:
                ran = None
        head = blocks_of((app.tests_dir / s).read_text(encoding="utf-8")) if s.endswith(".test.js") else []
        base_text = try_git("show", f"{frm}:tests/{s}")
        base_blocks = set(blocks_of(base_text)) if base_text is not None and s.endswith(".test.js") else set()
        # Without one result per block (a crash, tests made in a loop, a Python suite), the suite counts as one.
        if not ran or not head or len(ran) != len(head):
            results.append({"suite": s, "fails": code != 0, "summary": summary, "tests": None})
            continue
        tests = [{"name": ran[i]["name"], "fails": ran[i]["ok"] is False}
                 for i, b in enumerate(head) if b not in base_blocks]
        results.append({"suite": s, "fails": any(t["fails"] for t in tests), "summary": summary, "tests": tests})
finally:
    shutil.rmtree(tmp, ignore_errors=True)

out = []
for r in results:
    if r["tests"] is not None:
        out.append(f"  - {r['suite']}, on the base branch's code:")
        out.extend(f"      {'fails ' if t['fails'] else 'PASSES'}  {t['name']}" for t in r["tests"])
    else:
        out.append(f"  - {r['suite']} on the base branch's code: {'fails' if r['fails'] else 'passes'} ({r['summary']}), judged as a whole")
idle = [t for r in results for t in (r["tests"] or []) if not t["fails"]]
note = [f"  {len(idle)} new or changed test(s) pass on the base branch's code too (\"PASSES\" above): they don't test this change. "
        "Say in the pull request why each is needed, or remove it."] if idle else []
if any(r["fails"] for r in results):
    report("\n".join(["PASS  At least one new or changed test fails without this change, so it tests the change.", *out, *note]), 0)
report("\n".join([
    "FAIL  None of this pull request's new or changed tests fail on the base branch's code, so they don't test the change.",
    *out,
    "  Add a test that fails without your change. If the change really can't have one, explain why in the pull request;",
    "  the maintainer can allow it with the `no-test-needed` label.",
]), 1)
