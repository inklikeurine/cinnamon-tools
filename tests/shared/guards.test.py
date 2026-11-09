#!/usr/bin/python3
"""cinnamon-tools/tools/pr_guards.py and fail_first.py, on a throwaway applet
repo in a temporary group folder: each guard trips on what it should, stays
quiet on what it shouldn't, and gives way to its label. Run via:

    python3 ../cinnamon-tools/harness/run.py shared --tests --suite guards
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
passes = failures = 0


def eq(a, b, msg):
    global passes, failures
    if a == b:
        passes += 1
    else:
        failures += 1
        print(f"    FAIL {msg}\n      expected {b!r}\n      actual   {a!r}")


def ok(cond, msg):
    eq(bool(cond), True, msg)


def test(name):
    print(f"  {name}")


def sh(cwd, *args):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{args} failed: {r.stderr}")
    return r.stdout


UUID = "fake@inklikeurine"
APPLET_JS = """const Applet = imports.ui.applet;
const Utils = require('./modules/Utils');
class FakeApplet extends Applet.IconApplet {
    on_applet_removed_from_panel() {}
}
function main(metadata, orientation, panelHeight, instanceId) {
    return new FakeApplet(orientation, panelHeight, instanceId);
}
"""
UTILS_BUGGY = "function double(x) {\n    return x + x + 1;\n}\n"
UTILS_FIXED = "function double(x) {\n    return x + x;\n}\n"
SUITE_HEAD = """imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const Utils = T.makeRequire(ARGV[0])('./modules/Utils');

T.run('double of 0 is a number', () => {
    T.ok(typeof Utils.double(0) === 'number', 'number');
});
"""
SUITE_TAIL = "\nimports.system.exit(T.summary());\n"


def make_group(tmp: Path) -> Path:
    group = tmp / "cinnamon"
    shutil.copytree(TOOLS / "tools", group / "cinnamon-tools" / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(TOOLS / "harness", group / "cinnamon-tools" / "harness", ignore=shutil.ignore_patterns("__pycache__"))
    repo = group / "fake"
    files = repo / "files" / UUID
    (files / "modules").mkdir(parents=True)
    (files / "applet.js").write_text(APPLET_JS)
    (files / "modules" / "Utils.js").write_text(UTILS_BUGGY)
    (files / "metadata.json").write_text(json.dumps({"uuid": UUID, "name": "Fake", "description": "x", "version": "0.1.0",
                                                     "cinnamon-version": ["6.6"], "max-instances": 1}, indent=2) + "\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "basic.test.js").write_text(SUITE_HEAD + SUITE_TAIL)
    (repo / "tests" / "harness.json").write_text('{"tests": true}\n')
    (repo / "AGENTS.md").write_text("# AGENTS.md\n")
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    return repo


def branch(repo: Path, name: str):
    sh(repo, "git", "checkout", "-q", "-B", name, "main")


def commit(repo: Path):
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "change")


def guards(repo: Path, labels=(), author="someone"):
    env = {**os.environ, "PR_LABELS": json.dumps(list(labels)), "PR_AUTHOR": author}
    env.pop("GITHUB_STEP_SUMMARY", None)
    r = subprocess.run([sys.executable, str(repo.parent / "cinnamon-tools" / "tools" / "pr_guards.py"), "main"],
                       cwd=repo, capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def fail_first(repo: Path, labels=()):
    env = {**os.environ, "PR_LABELS": json.dumps(list(labels))}
    env.pop("GITHUB_STEP_SUMMARY", None)
    r = subprocess.run([sys.executable, str(repo.parent / "cinnamon-tools" / "tools" / "fail_first.py"), "fake", "main"],
                       cwd=repo, capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


with tempfile.TemporaryDirectory(prefix="guards-test-") as tmp:
    repo = make_group(Path(tmp))
    utils = repo / "files" / UUID / "modules" / "Utils.js"
    suite = repo / "tests" / "basic.test.js"

    test("guards: a plain code change passes")
    branch(repo, "plain")
    utils.write_text(UTILS_FIXED)
    commit(repo)
    code, out = guards(repo)
    eq(code, 0, "exit 0\n" + out)
    ok("PASS" in out, "says PASS")

    test("guards: adding a test passes, editing an existing one needs tests-changed")
    branch(repo, "add-test")
    suite.write_text(SUITE_HEAD + "\nT.run('another', () => { T.ok(true, 'x'); });\n" + SUITE_TAIL)
    commit(repo)
    eq(guards(repo)[0], 0, "adding lines to tests/ is fine")
    branch(repo, "edit-test")
    suite.write_text(SUITE_HEAD.replace("is a number", "is numeric") + SUITE_TAIL)
    commit(repo)
    code, out = guards(repo)
    eq(code, 1, "editing a test fails")
    ok("Existing tests were changed" in out and "tests/basic.test.js" in out, "names the test file")
    eq(guards(repo, ["tests-changed"])[0], 0, "the tests-changed label allows it")

    test("guards: new programs, services, web addresses, network and metadata need new-access")
    cases = {
        "spawn": ("Spawn.run(['rfkill', 'list']);\n", "runs a new program rfkill"),
        "cmdline": ("GLib.spawn_command_line_async('xdg-open /tmp');\n", "runs a new program xdg-open"),
        "path": ("const P = '/usr/bin/pactl';\n", "runs a new program pactl"),
        "service": ("const N = 'org.bluez.Device1';\n", "uses a new system service or settings store org.bluez.Device1"),
        "web": ("const U = 'https://example.org/feed';\n", "new web address example.org"),
        "soup": ("const Soup = imports.gi.Soup;\n", "uses a network library for the first time Soup"),
    }
    for name, (line, expect) in cases.items():
        branch(repo, f"access-{name}")
        utils.write_text(UTILS_BUGGY + line)
        commit(repo)
        code, out = guards(repo)
        eq(code, 1, f"{name}: fails\n{out}")
        ok(expect in out, f"{name}: names it ({expect})")
        eq(guards(repo, ["new-access"])[0], 0, f"{name}: the new-access label allows it")
    branch(repo, "access-role")
    meta = json.loads((repo / "files" / UUID / "metadata.json").read_text())
    meta["role"] = "notifications"
    (repo / "files" / UUID / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    commit(repo)
    code, out = guards(repo)
    eq(code, 1, "a new role fails")
    ok('metadata.json "role"' in out, "names the role")
    branch(repo, "access-tests-only")
    (repo / "tests" / "net.test.js").write_text("const U = 'https://example.org/'; Spawn.run(['curl']);\n")
    commit(repo)
    eq(guards(repo)[0], 0, "web addresses and programs in tests/ are not the applet's reach")

    test("guards: rule files need rules-changed, except Dependabot's .github updates")
    branch(repo, "rules")
    (repo / "AGENTS.md").write_text("# AGENTS.md\nchanged\n")
    commit(repo)
    code, out = guards(repo)
    eq(code, 1, "changing AGENTS.md fails")
    ok("AGENTS.md: changed" in out, "names AGENTS.md")
    eq(guards(repo, ["rules-changed"])[0], 0, "the rules-changed label allows it")
    branch(repo, "dependabot")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "checks.yml").write_text("name: Checks\n")
    commit(repo)
    eq(guards(repo, author="dependabot[bot]")[0], 0, "Dependabot may change .github/")
    eq(guards(repo, author="someone")[0], 1, "anyone else may not")

    test("fail-first: a fix with a test that fails on main passes")
    branch(repo, "ff-good")
    utils.write_text(UTILS_FIXED)
    suite.write_text(SUITE_HEAD + "\nT.run('double of 2 is 4', () => {\n    T.eq(Utils.double(2), 4, 'four');\n});\n" + SUITE_TAIL)
    commit(repo)
    code, out = fail_first(repo)
    eq(code, 0, "exit 0\n" + out)
    ok("fails   double of 2 is 4" in out or "fails  double of 2 is 4" in out, "lists the new test as failing on main\n" + out)

    test("fail-first: a fix whose new test also passes on main fails, and names it")
    branch(repo, "ff-idle")
    utils.write_text(UTILS_FIXED)
    suite.write_text(SUITE_HEAD + "\nT.run('double of 1 is a number', () => {\n    T.ok(typeof Utils.double(1) === 'number', 'n');\n});\n" + SUITE_TAIL)
    commit(repo)
    code, out = fail_first(repo)
    eq(code, 1, "exit 1\n" + out)
    ok("PASSES  double of 1 is a number" in out, "names the test that passes on main")
    eq(fail_first(repo, ["no-test-needed"])[0], 0, "the no-test-needed label allows it")

    test("fail-first: a code change with no test change fails; no code change passes; a version bump passes")
    branch(repo, "ff-none")
    utils.write_text(UTILS_FIXED)
    commit(repo)
    code, out = fail_first(repo)
    eq(code, 1, "no test → fail")
    ok("adds or changes no test suite" in out, "says why")
    branch(repo, "ff-readme")
    (repo / "README.md").write_text("# Fake\n")
    commit(repo)
    eq(fail_first(repo)[0], 0, "README only → nothing to prove")
    branch(repo, "ff-release")
    meta = json.loads((repo / "files" / UUID / "metadata.json").read_text())
    meta["version"] = "0.2.0"
    (repo / "files" / UUID / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    commit(repo)
    code, out = fail_first(repo)
    eq(code, 0, "version-only change → a release")
    ok("a release" in out, "says it's a release")

print(("PASSED" if not failures else "FAILED") + f": {passes} assertions ok, {failures} failed")
sys.exit(1 if failures else 0)
