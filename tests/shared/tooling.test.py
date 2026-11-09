#!/usr/bin/python3
"""cinnamon-tools/tools/lib/shared.py: header parsing, staging, sync and drift
detection on a temporary applet tree. Run via:
    python3 ../cinnamon-tools/harness/run.py shared --tests --suite tooling
argv[1] = the staged dir (unused: this suite builds its own temp tree)."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
TOOLS = HERE.parents[2]
sys.path.insert(0, str(TOOLS / "tools"))
from lib import shared  # noqa: E402
from lib.applet import Applet  # noqa: E402

failures = 0
passes = 0


def eq(a, b, msg):
    global failures, passes
    if a == b:
        passes += 1
    else:
        failures += 1
        print(f"    FAIL {msg}\n      expected {b!r}\n      actual   {a!r}")


def ok(cond, msg):
    eq(bool(cond), True, msg)


def test(name):
    print("  " + name)


test("split_header / render_copy round trip")
body = "const x = 1;\n"
h = shared.body_hash(body)
copy = shared.render_copy("Foo", body, h)
name, hh, b = shared.split_header(copy)
eq((name, hh, b), ("Foo", h, body), "copy header parses")
eq(shared.split_header("// @shared Foo.js\n" + body), ("Foo", None, body), "canonical header parses")
eq(shared.split_header("// nothing\n" + body)[0], None, "no header → None")

test("canonical modules exist and carry the header")
names = shared.canonical_names()
ok("Spawn" in names and "DBusProxy" in names, "Spawn and DBusProxy are canonical")
for n in names:
    b, hsh = shared.canonical_body(n)
    eq(len(hsh), 64, f"{n}: sha256")
    ok(b.endswith("\n"), f"{n}: trailing newline")

test("sync into a temporary applet tree, then detect drift")
with tempfile.TemporaryDirectory(prefix="shared-test-") as tmp:
    tmp = Path(tmp)
    uuid = "fake@inklikeurine"
    app = Applet(uuid)
    # Redirect the applet's paths into the temp tree.
    app.applet_dir = tmp / uuid / "files" / uuid
    app.spec_path = tmp / "tests" / "harness.json"
    app.applet_dir.mkdir(parents=True)
    app.spec_path.parent.mkdir(parents=True)
    app.spec_path.write_text(json.dumps({"shared": ["Spawn", "DBusProxy.js"]}))
    eq(shared.declared_modules(app), ["Spawn", "DBusProxy"], "declared list, .js stripped")

    recs = shared.sync_applet(app, write=False)
    eq([r["action"] for r in recs], ["would-create", "would-create"], "check mode creates nothing")
    ok(not shared.copy_path(app.applet_dir, "Spawn").exists(), "no file written in check mode")

    recs = shared.sync_applet(app, write=True)
    eq([r["action"] for r in recs], ["created", "created"], "first sync creates")
    p = shared.copy_path(app.applet_dir, "Spawn")
    ok(p.exists(), "copy written")
    eq(shared.inspect_copy(p)["state"], "ok", "fresh copy is ok")
    eq([r["action"] for r in shared.sync_applet(app)], ["unchanged", "unchanged"], "second sync unchanged")

    # Edit the copy → drift.
    p.write_text(p.read_text() + "// local edit\n")
    eq(shared.inspect_copy(p)["state"], "drift", "edited copy is drift")
    eq([r["action"] for r in shared.sync_applet(app)], ["updated", "unchanged"], "sync overwrites the edited copy")
    eq(shared.inspect_copy(p)["state"], "ok", "ok again after sync")

    # Stale header hash but identical body.
    n_, h_, b_ = shared.split_header(p.read_text())
    p.write_text(shared.render_copy("Spawn", b_, "0" * 64))
    eq(shared.inspect_copy(p)["state"], "stale", "old header hash → stale")

    # Stray file and no-header file.
    stray = app.applet_dir / "modules" / "shared" / "Extra.js"
    stray.write_text("// @shared Extra.js " + "1" * 64 + " — x\nvar y = 2;\n")
    eq([s.name for s in shared.stray_copies(app)], ["Extra.js"], "undeclared copy is stray")
    eq(shared.inspect_copy(stray)["state"], "no-canonical", "declared-looking copy without canonical")
    noh = app.applet_dir / "modules" / "shared" / "Plain.js"
    noh.write_text("var z = 3;\n")
    eq(shared.inspect_copy(noh)["state"], "no-header", "file without header")

    # Declared module without a canonical file raises.
    app.spec_path.write_text(json.dumps({"shared": ["Nope"]}))
    try:
        shared.sync_applet(app)
        ok(False, "should raise for a missing canonical")
    except shared.SharedError:
        ok(True, "raises SharedError for a missing canonical")

    # Bad declaration type raises.
    app.spec_path.write_text(json.dumps({"shared": "Spawn"}))
    try:
        shared.declared_modules(app)
        ok(False, "should raise for a non-list")
    except shared.SharedError:
        ok(True, "raises SharedError for a non-list")

test("stage() writes every canonical module as a copy")
with tempfile.TemporaryDirectory(prefix="shared-stage-") as tmp:
    written = shared.stage(Path(tmp) / "st")
    eq(sorted(p.stem for p in written), names, "all modules staged")
    for p in written:
        eq(shared.inspect_copy(p)["state"], "ok", f"{p.name} staged copy ok")

test("new_applet.py --shared creates an applet repo, declares and vendors modules (in a throwaway group folder)")
import re
import shutil
import subprocess
with tempfile.TemporaryDirectory(prefix="scaffold-") as tmp:
    group = Path(tmp) / "cinnamon"
    ws = group / "cinnamon-tools"
    for sub in ("tools", "templates", "shared"):
        shutil.copytree(TOOLS / sub, ws / sub, ignore=shutil.ignore_patterns("__pycache__"))

    def scaffold(*args):
        return subprocess.run([sys.executable, str(ws / "tools" / "new_applet.py"), *args],
                              capture_output=True, text=True, cwd=group)

    r = scaffold("fakevpn", "Toggles fake VPN profiles", "--shared", "Spawn,DBusProxy")
    eq(r.returncode, 0, "scaffold exits 0 (check.py passed on the copy)\n" + r.stdout + r.stderr)
    u = "fakevpn@inklikeurine"
    spec = json.loads((group / "fakevpn" / "tests" / "harness.json").read_text())
    eq(spec.get("shared"), ["Spawn", "DBusProxy"], "tests/harness.json declares the modules")
    for n in ("Spawn", "DBusProxy"):
        cp = group / "fakevpn" / "files" / u / "modules" / "shared" / f"{n}.js"
        ok(cp.exists(), f"{n}.js vendored")
        if cp.exists():
            hdr = cp.read_text(encoding="utf-8").splitlines()[0]
            ok(hdr.startswith(f"// @shared {n}.js ") and "vendored copy" in hdr, f"{n}.js has the copy header")
    ok((group / "fakevpn" / "tests" / "fixtures").is_dir(), "tests/fixtures/ created in the applet repo")
    ok((group / "fakevpn" / "tests" / "smoke.test.js").exists(), "smoke test in the applet repo")
    ok(f"shared: Spawn created" in r.stdout, "reports what it vendored")
    repo = group / "fakevpn"
    ok((repo / ".git").is_dir(), "the applet is its own git repo")
    for f in ("AGENTS.md", "CLAUDE.md", "UNLICENSE", ".gitignore", ".claude/settings.json",
              ".github/workflows/checks.yml", ".github/workflows/guards.yml", ".github/pull_request_template.md", ".github/dependabot.yml"):
        ok((repo / f).exists(), f"repo file {f}")
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    ok(not re.search(r"^@\S", agents, re.M), "AGENTS.md is self-contained (no @ imports)")
    ok("fakevpn@inklikeurine" in agents and "__UUID__" not in agents, "AGENTS.md placeholders filled")
    ok("also read\n`../_workshop/AGENTS.md`" in agents, "AGENTS.md ends by pointing at the private workshop")
    ok("run.py fakevpn@inklikeurine --tests" in (repo / ".github/workflows/checks.yml").read_text(encoding="utf-8"), "checks.yml filled for this applet")
    ok("https://github.com/inklikeurine/cinnamon-fakevpn" in (repo / "README.md").read_text(encoding="utf-8"), "README links its own repo")

    r = scaffold("other", "Another one", "--shared", "Nope")
    eq(r.returncode, 2, "unknown module name is a usage error")
    ok("no canonical module Nope" in r.stderr, "names the unknown module")
    ok(not (group / "other").exists(), "nothing scaffolded on error")

    r = scaffold("plain", "No shared modules")
    eq(r.returncode, 0, "without --shared still works\n" + r.stdout + r.stderr)
    spec = json.loads((group / "plain" / "tests" / "harness.json").read_text())
    eq(spec.get("shared"), [], "harness.json shared stays empty")
    ok(not (group / "plain" / "files" / "plain@inklikeurine" / "modules").exists(), "no modules/ dir")

print(("PASSED" if not failures else "FAILED") + f": {passes} assertions ok, {failures} failed")
sys.exit(1 if failures else 0)
