#!/usr/bin/python3
"""Test harness for one applet: headless suites and a live check in the
running Cinnamon session. Standard library + PyGObject (Gio) only.

    python3 ../cinnamon-tools/harness/run.py <uuid> [--tests] [--live] [--spec]
        [--wait <seconds>] [--pre '<js>']... [--eval '<js expr>']... [--post '<js>']... [--screenshot]
        [--panel <id>] [--keep] [--no-install] [--uninstall] [--suite <name>]
    python3 ../cinnamon-tools/harness/run.py shared [--tests] [--suite <name>]

  --tests        run <applet>/tests/*.test.js under cjs and *.test.py under
                 /usr/bin/python3 (headless; no Cinnamon needed). A .js suite gets
                 ARGV = [appletDir, harnessDir, testsDir], a .py suite
                 argv[1:] = [appletDir, testsDir]; fixtures live in
                 <applet>/tests/fixtures/ (T.fixture(name) in lib.js)
  --live         install (symlink), make sure one instance is on a panel,
                 ReloadXlet over DBus, wait, probe the extension state with Eval,
                 run --eval expressions (each must return exactly true), read the
                 lines ~/.xsession-errors gained meanwhile, optionally screenshot
  --spec         take tests/live/wait/evals/screenshot from
                 <applet>/tests/harness.json; CLI flags win
  --pre          JS expression evaluated inside Cinnamon BEFORE the reload, once the
                 applet is on a panel; must return exactly true. Use it to stash the
                 running instance (globalThis.__prev = …) so an --eval after the
                 reload can prove the old instance was torn down
  --post         JS expression evaluated AFTER the applet was removed from the panel
                 again (step 9). Proves what removal restored. Must return exactly
                 true. Recorded as skipped when nothing was removed (--use-existing,
                 --keep), since there is no removal to prove (2026-10-12)
  sleep <s>      in place of any --pre/--eval/--post expression: pause that many
                 seconds inside the sequence (Cinnamon keeps running), for checks
                 that need an async round trip started by an earlier expression
  --wait         settle time after the reload (default 3 s)
  --panel        panel id to add the applet to if it is not on one (default: first enabled)
  --keep         leave the applet on the panel if this run added it. Without it the
                 applet's settings files (~/.config/cinnamon/spices/<uuid>/) are put back
                 exactly as they were before the run — Cinnamon keeps them across removal,
                 and evals that call setValue() would otherwise leak into the owner's next
                 instance (2026-09-19: a run switched the owner's DND triggers off)
  --runtime-dir  a directory the applet writes runtime data to (~/.local/share/<name>,
                 ~/.cache/<name>); repeatable, or "runtimeDirs" in harness.json. Snapshotted
                 before the live run and put back after the instance is removed, like the
                 settings files, so test records never reach the owner's next instance
  --use-existing run the pre/eval/post expressions against an instance that is
                 ALREADY on a panel (the owner's). Off by default: the harness
                 then refuses, because evals toggle state, clear lists and write
                 settings on whatever instance they find (2026-09-19 lesson).
                 Afterwards its settings files and runtime dirs are restored like a
                 harness instance's: unload, restore, load (2026-10-12). Read the
                 spec first: an eval can still assume a fresh, empty instance
  --no-install   do not create/repoint the symlink (fail if not installed)
  --uninstall    after the run, remove the instance this run added AND the symlink
  --suite        run only tests/<uuid>/<name>.test.js|.py
  --no-reset-imports
                 skip dropping the applet's cached `imports.<dir>.*` modules before
                 the reload (done automatically when applet.js uses that pattern)

  shared         pseudo-uuid: run cinnamon-tools/tests/shared/ against the canonical
                 modules in cinnamon-tools/shared/. They are staged as vendored copies
                 into cinnamon-tools/out/shared/staging/modules/shared/ first, so a suite
                 loads them exactly as an applet would:
                 T.makeRequire(ARGV[0])('./modules/shared/<Name>'). Headless only.

Output: cinnamon-tools/out/<uuid>/{tests.log, live.log, panel.png, applet.png,
result.json} and a PASS/FAIL line. Exit 0 = PASS, 1 = FAIL, 2 = usage.

What the live check does NOT prove: appearance, real clicks, the settings
dialog, behaviour across a Cinnamon restart. That stays Manual Review.
See AGENTS.md → "Verification → Live check" for the rules this obeys.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.applet import TOOLS, PYTHON, GROUP_ROOT, USER_APPLETS_DIR, UUID_SUFFIX, Applet, load_json, tree_sha256  # noqa: E402
from lib.shared import SHARED_DIR, SharedError, stage  # noqa: E402

HARNESS_DIR = Path(__file__).resolve().parent


def cinnamon_log_path() -> Path:
    """The file Cinnamon's stderr goes to. Normally ~/.xsession-errors, but a
    Cinnamon started by hand (`cinnamon --replace` after a crash) writes wherever
    that shell pointed fd 2, so follow /proc/<pid>/fd/2 when it is a plain file."""
    try:
        pids = subprocess.run(["pgrep", "-x", "cinnamon"], capture_output=True, text=True).stdout.split()
        for pid in pids:
            target = os.readlink(f"/proc/{pid}/fd/2")
            if target.startswith("/") and Path(target).is_file():
                return Path(target)
    except OSError:
        pass
    return Path.home() / ".xsession-errors"


XSESSION_ERRORS = cinnamon_log_path()

# ---------------------------------------------------------------------------
# args
# ---------------------------------------------------------------------------
argv = sys.argv[1:]
opts = {"tests": False, "live": False, "spec": False, "wait": None, "evals": [], "pre": [], "post": [], "screenshot": None,
        "panel": None, "keep": False, "install": True, "uninstall": False, "suite": None, "use_existing": False, "runtime_dirs": []}
uuid = None


def usage(msg: str):
    print(f"run.py: {msg}\nusage: run.py <uuid> [--tests] [--live] [--spec] [--wait s] [--pre expr]... [--eval expr]... [--post expr]... [--screenshot] [--panel id] [--keep] [--no-install] [--uninstall] [--suite name]", file=sys.stderr)
    sys.exit(2)


i = 0
while i < len(argv):
    a = argv[i]

    def nxt():
        global i
        if i + 1 >= len(argv):
            usage(f"{a} needs a value")
        i += 1
        return argv[i]

    if a == "--tests": opts["tests"] = True
    elif a == "--live": opts["live"] = True
    elif a == "--spec": opts["spec"] = True
    elif a == "--wait": opts["wait"] = float(nxt())
    elif a == "--eval": opts["evals"].append(nxt())
    elif a == "--pre": opts["pre"].append(nxt())
    elif a == "--post": opts["post"].append(nxt())
    elif a == "--screenshot": opts["screenshot"] = True
    elif a == "--panel": opts["panel"] = int(nxt())
    elif a == "--keep": opts["keep"] = True
    elif a == "--no-install": opts["install"] = False
    elif a == "--uninstall": opts["uninstall"] = True
    elif a == "--suite": opts["suite"] = nxt()
    elif a == "--no-reset-imports": opts["no_reset_imports"] = True
    elif a == "--use-existing": opts["use_existing"] = True
    elif a == "--runtime-dir": opts["runtime_dirs"].append(nxt())
    elif a.startswith("--"): usage(f"unknown option {a}")
    elif uuid: usage("only one uuid")
    else: uuid = a
    i += 1
if not uuid:
    usage("uuid required")
if uuid != "shared" and "@" not in uuid:
    uuid += UUID_SUFFIX          # 'hush' → 'hush@inklikeurine'


class SharedTarget:
    """Stand-in for Applet when testing cinnamon-tools/shared/: the staged tree plays
    the applet dir, cinnamon-tools/tests/shared/ the tests dir."""
    uuid = "shared"
    applet_dir = TOOLS / "out" / "shared" / "staging"
    tests_dir = TOOLS / "tests" / "shared"
    spec_path = TOOLS / "tests" / "shared" / "harness.json"
    out_dir = TOOLS / "out" / "shared"

    def metadata(self):
        return None


if uuid == "shared":
    if opts["live"] or opts["spec"] or opts["evals"] or opts["pre"] or opts["post"] or opts["screenshot"]:
        usage("'shared' supports only --tests [--suite <name>]")
    if not SHARED_DIR.is_dir():
        usage("cinnamon-tools/shared/ does not exist")
    app = SharedTarget()
    try:
        staged = stage(app.applet_dir)
    except SharedError as e:
        usage(str(e))
    print(f"staged {len(staged)} shared module(s) into {app.applet_dir.relative_to(GROUP_ROOT)}/modules/shared/")
else:
    app = Applet(uuid)
    if not app.applet_dir.is_dir():
        usage(f"{uuid}/files/{uuid}/ does not exist")

spec = None
if opts["spec"]:
    sp = app.spec_path
    if not sp.exists():
        usage(f"--spec: {sp.relative_to(GROUP_ROOT)} does not exist")
    spec = load_json(sp)
    if not isinstance(spec, dict):
        usage(f"--spec: {sp.relative_to(GROUP_ROOT)} is not a JSON object")
    if not opts["tests"] and not opts["live"]:
        opts["tests"] = bool(spec.get("tests", True))
        opts["live"] = bool(spec.get("live", False))
    if opts["wait"] is None and spec.get("wait") is not None:
        opts["wait"] = float(spec["wait"])
    if not opts["evals"] and isinstance(spec.get("evals"), list):
        opts["evals"] = [str(e) for e in spec["evals"]]
    if not opts["pre"] and isinstance(spec.get("pre"), list):
        opts["pre"] = [str(e) for e in spec["pre"]]
    if not opts["post"] and isinstance(spec.get("post"), list):
        opts["post"] = [str(e) for e in spec["post"]]
    if not opts["runtime_dirs"] and isinstance(spec.get("runtimeDirs"), list):
        opts["runtime_dirs"] = [str(d) for d in spec["runtimeDirs"]]
    if opts["screenshot"] is None and spec.get("screenshot") is not None:
        opts["screenshot"] = bool(spec["screenshot"])
if not opts["tests"] and not opts["live"]:
    opts["tests"] = True
opts["wait"] = 3.0 if opts["wait"] is None else opts["wait"]
opts["screenshot"] = bool(opts["screenshot"])

app.out_dir.mkdir(parents=True, exist_ok=True)
meta = app.metadata() or {}
result = {
    "uuid": uuid,
    "version": meta.get("version"),
    "treeSha256": tree_sha256(SHARED_DIR if uuid == "shared" else app.applet_dir),
    "spec": str(app.spec_path.relative_to(GROUP_ROOT)) if spec else None,
    "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "tests": {"ran": False},
    "live": {"ran": False},
    "pass": False,
}
failures: list[str] = []


def say(msg: str):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# headless tests
# ---------------------------------------------------------------------------
def run_tests():
    tdir = app.tests_dir
    log = app.out_dir / "tests.log"
    suites = []
    if tdir.is_dir():
        suites = sorted(list(tdir.glob("*.test.js")) + list(tdir.glob("*.test.py")))
    if opts["suite"]:
        suites = [s for s in suites if s.name.split(".test.")[0] == opts["suite"]]
    r = {"ran": True, "dir": str(tdir.relative_to(GROUP_ROOT)), "suites": [], "pass": True}
    if not suites:
        r["pass"] = False
        failures.append(f"no test suites in {r['dir']} (AGENTS.md requires at least the smoke test)")
    lines = []
    for s in suites:
        name = s.name
        # ARGV: applet dir, harness dir (cjs only), tests dir (for fixtures/)
        if name.endswith(".test.js"):
            cmd = ["cjs", str(s), str(app.applet_dir), str(HARNESS_DIR), str(tdir)]
        else:
            cmd = [PYTHON, "-B", str(s), str(app.applet_dir), str(tdir)]
        say(f"== {name}")
        t0 = time.time()
        try:
            res = subprocess.run(cmd, cwd=GROUP_ROOT, capture_output=True, text=True, timeout=180,
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            out, err, code = res.stdout, res.stderr, res.returncode
        except FileNotFoundError:
            out, err, code = "", f"{cmd[0]} not found", 127
        except subprocess.TimeoutExpired as e:
            out, err, code = (e.stdout or ""), "TIMEOUT after 180 s", 124
        ok = code == 0
        r["suites"].append({"name": name, "pass": ok, "exit": code, "seconds": round(time.time() - t0, 1)})
        r["pass"] = r["pass"] and ok
        lines.append(f"=== {name} (exit {code}) ===\n{out}{err}\n")
        say(out.rstrip())
        if err.strip():
            say(err.rstrip())
        if not ok:
            failures.append(f"suite {name} failed (exit {code})")
    log.write_text("".join(lines))
    result["tests"] = r


# ---------------------------------------------------------------------------
# live check
# ---------------------------------------------------------------------------
def run_live():
    from gi.repository import Gio, GLib  # noqa: E402  (PyGObject is on Mint by default)

    r = {"ran": True, "pass": True, "installed": None, "addedToPanel": False, "panel": None,
         "probe": None, "preChecks": [], "checks": [], "postChecks": [], "logFile": str(XSESSION_ERRORS), "logLines": [], "screenshots": []}
    result["live"] = r
    log: list[str] = []

    def fail(msg: str):
        r["pass"] = False
        failures.append(msg)
        log.append("FAIL " + msg)
        say("FAIL " + msg)

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method: str, params=None, timeout_ms: int = 15000):
        return bus.call_sync("org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", method, params, None,
                             Gio.DBusCallFlags.NONE, timeout_ms, None)

    def ev(script: str):
        """org.Cinnamon.Eval → (success, value). Cinnamon returns
        JSON.stringify(eval(script)) ('' for undefined); decode it once.
        On failure the string is the JSON of the thrown error. Needs
        development-tools."""
        ok, out = call("Eval", GLib.Variant("(s)", [script])).unpack()
        if out == "":
            return ok, None
        try:
            return ok, json.loads(out)
        except ValueError:
            return ok, out

    # 0. session sanity
    try:
        cinnamon_version = bus.call_sync("org.Cinnamon", "/org/Cinnamon", "org.freedesktop.DBus.Properties", "Get",
                                         GLib.Variant("(ss)", ["org.Cinnamon", "CinnamonVersion"]), None,
                                         Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error as e:
        fail(f"org.Cinnamon is not on the session bus ({e.message}); is a Cinnamon session running?")
        return
    r["cinnamonVersion"] = cinnamon_version
    settings = Gio.Settings.new("org.cinnamon")
    if not settings.get_boolean("development-tools"):
        fail("org.cinnamon development-tools is false; Eval is disabled (gsettings set org.cinnamon development-tools true)")
        return

    # 1. install (symlink)
    link = USER_APPLETS_DIR / uuid
    target = app.applet_dir.resolve()
    if link.is_symlink():
        if link.resolve() != target:
            if not opts["install"]:
                fail(f"{link} points at {link.resolve()}, not the repo; run without --no-install to repoint")
                return
            link.unlink()
            link.symlink_to(target)
            log.append(f"repointed symlink {link} -> {target}")
        r["installed"] = "symlink"
    elif link.exists():
        fail(f"{link} is a real directory, not a symlink; refusing to replace it (remove it yourself if it is a stale copy)")
        return
    else:
        if not opts["install"]:
            fail(f"{link} does not exist and --no-install was given")
            return
        USER_APPLETS_DIR.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        log.append(f"created symlink {link} -> {target}")
        r["installed"] = "symlink"

    # 2. on a panel? Snapshot the settings files first: Cinnamon keeps
    #    ~/.config/cinnamon/spices/<uuid>/*.json across removal, and this run's
    #    evals may write to them (AppletSettings.setValue) — they must not leak
    #    into the instance the owner adds afterwards.
    spices_dir = Path.home() / ".config" / "cinnamon" / "spices" / uuid
    settings_before = {f.name: f.read_bytes() for f in spices_dir.glob("*.json")} if spices_dir.is_dir() else None
    r["settingsSnapshot"] = sorted(settings_before) if settings_before is not None else None
    # Same for the applet's runtime data directories (harness.json "runtimeDirs").
    import filecmp
    import shutil
    import tempfile
    runtime_snap = []   # (dir, existed, backup path or None)
    snap_root = Path(tempfile.mkdtemp(prefix="harness-runtime-")) if opts["runtime_dirs"] else None
    for d in opts["runtime_dirs"]:
        rt_dir = Path(os.path.expanduser(d))
        if rt_dir.is_dir():
            backup = snap_root / str(len(runtime_snap))
            shutil.copytree(rt_dir, backup, symlinks=True)
            runtime_snap.append((rt_dir, True, backup))
        else:
            runtime_snap.append((rt_dir, False, None))
    r["runtimeSnapshot"] = [str(t) + (" (existed)" if e else " (absent)") for t, e, _ in runtime_snap]
    enabled_before = settings.get_strv("enabled-applets")
    entries = [e for e in enabled_before if e.split(":")[3] == uuid] if enabled_before else []
    if not entries:
        panels = settings.get_strv("panels-enabled")  # "id:monitor:position"
        panel_ids = [int(p.split(":")[0]) for p in panels]
        if not panel_ids:
            fail("no panels enabled; cannot add the applet anywhere")
            return
        panel = opts["panel"] or panel_ids[0]
        if panel not in panel_ids:
            fail(f"panel {panel} is not enabled (have {panel_ids})")
            return
        next_id = settings.get_int("next-applet-id")
        # order = 1 + highest order in that panel's right zone
        orders = [int(e.split(":")[2]) for e in enabled_before if e.startswith(f"panel{panel}:right:")]
        order = (max(orders) + 1) if orders else 0
        entry = f"panel{panel}:right:{order}:{uuid}:{next_id}"
        settings.set_int("next-applet-id", next_id + 1)
        settings.set_strv("enabled-applets", enabled_before + [entry])
        Gio.Settings.sync()
        r["addedToPanel"] = True
        r["panel"] = panel
        log.append(f"added {entry} to enabled-applets (will restore unless --keep)")
        say(f"added {uuid} to panel {panel} as instance {next_id}")
        time.sleep(1.0)  # let appletManager pick up the change before we reload
    else:
        r["panel"] = int(entries[0].split(":")[0].replace("panel", ""))
        if (opts["pre"] or opts["evals"] or opts["post"]) and not opts["use_existing"]:
            fail(f"{uuid} is already on panel {r['panel']} (instance {entries[0].split(':')[4]}) and this run has "
                 "pre/eval/post expressions, which act on whatever instance they find; remove it from the panel "
                 "first, or pass --use-existing if that instance may be driven")
            return
        log.append(f"note: driving the instance already on panel {r['panel']} (--use-existing)")

    def run_exprs(exprs, tag, bucket, what):
        """Evaluate each expression inside Cinnamon; each must return exactly
        true. 'sleep <s>' pauses instead (for async round trips)."""
        for expr in exprs:
            m = re.fullmatch(r"sleep\s+([0-9.]+)", expr.strip())
            if m:
                secs = float(m.group(1))
                time.sleep(secs)
                bucket.append({"expr": expr, "ok": True, "value": f"slept {secs}s"})
                say(f"{tag} ok   sleep {secs}s")
                continue
            try:
                ok, out = ev(f"(function(){{ try {{ return ({expr}); }} catch (e) {{ return 'THREW ' + e.message; }} }})()")
            except GLib.Error as e:
                ok, out = False, e.message
            passed = ok and out is True
            shown = json.dumps(out) if not isinstance(out, str) else out
            bucket.append({"expr": expr, "ok": passed, "value": shown})
            say((f"{tag} ok   " if passed else f"{tag} FAIL ") + expr + ("" if passed else f"  → {shown}"))
            if not passed:
                fail(f"{what} eval returned {shown}: {expr}")

    # 2b. Warm-up reload. Cinnamon never unloads an applet's module when its
    #     last instance is removed (appletManager.onEnabledAppletsChanged calls
    #     removeAppletFromPanels, not unloadExtension), so an instance added now
    #     runs whatever code was loaded earlier in this session. One ReloadXlet
    #     makes the pre-phase instance run the files on disk; the measured
    #     reload below then exercises teardown + reload of *current* code.
    try:
        call("ReloadXlet", GLib.Variant("(ss)", [uuid, "APPLET"]))
        log.append(f"warm-up ReloadXlet({uuid}) so the pre-phase instance runs the code on disk")
        time.sleep(max(1.5, opts["wait"] / 2))
    except GLib.Error as e:
        fail(f"warm-up ReloadXlet failed: {e.message}")
        return

    # 3a. pre-reload evals (the instance is on the panel by now)
    run_exprs(opts["pre"], "pre ", r["preChecks"], "pre-reload")

    # 3b. refresh GJS-importer caches for local module dirs (see docs/cinnamon-reference.md §1):
    #    `imports.<dir>.<Module>` objects live for the whole Cinnamon process and their
    #    entries are non-configurable (delete is a silent no-op), so a plain reload would
    #    run the new applet.js against old modules. The root importer's properties ARE
    #    writable, and Cinnamon drops imports.applets[<uuid>] on every unload, so a fresh
    #    sub-importer can be taken from there and assigned over the stale one. Applets
    #    that use Cinnamon's require() loader never need this.
    r["importerReset"] = []
    if not opts.get("no_reset_imports"):
        src = app.applet_js.read_text(encoding="utf-8", errors="replace") if app.applet_js.exists() else ""
        if "imports.searchPath.unshift(" in src:
            local_dirs = {d.name for d in app.applet_dir.iterdir() if d.is_dir() and any(d.glob("*.js"))}
            used = {m for m in re.findall(r"\bimports\.([A-Za-z_]\w*)\.", src) if m in local_dirs}
            for d in sorted(used):
                js = ("(function(){ try { const fresh = imports.applets['" + uuid + "']['" + d + "'];"
                      " const stale = imports['" + d + "']; if (stale === fresh) return 'already fresh';"
                      " imports['" + d + "'] = fresh; return imports['" + d + "'] === fresh ? 'refreshed' : 'assignment ignored';"
                      " } catch (e) { return 'failed: ' + e.message; } })()")
                try:
                    ok, out = ev(js)
                    r["importerReset"].append({"dir": d, "result": out if ok else str(out)})
                    log.append(f"importer refresh imports.{d}: {out}")
                    if out not in ("refreshed", "already fresh"):
                        fail(f"could not refresh imports.{d} before the reload ({out}); the reload would run against stale modules")
                except GLib.Error as e:
                    fail(f"importer refresh imports.{d} failed: {e.message}")

    # 4. reload
    mark = XSESSION_ERRORS.stat().st_size if XSESSION_ERRORS.exists() else 0
    t_reload = time.time()
    try:
        call("ReloadXlet", GLib.Variant("(ss)", [uuid, "APPLET"]))
        log.append(f"ReloadXlet({uuid}, APPLET)")
    except GLib.Error as e:
        fail(f"ReloadXlet failed: {e.message}")
    time.sleep(opts["wait"])

    # 5. log delta (read first so a failing probe still comes with the log)
    if XSESSION_ERRORS.exists():
        with open(XSESSION_ERRORS, "rb") as f:
            f.seek(mark)
            delta = f.read().decode("utf-8", errors="replace")
        short = uuid.split("@")[0]
        keep = [ln for ln in delta.splitlines() if uuid in ln or f"[{short}]" in ln or "JS ERROR" in ln or "LookingGlass/error" in ln]
        r["logLines"] = keep[-200:]
        bad = [ln for ln in keep if ("JS ERROR" in ln or "LookingGlass/error" in ln) and (uuid in ln or f"[{short}]" in ln or short in ln.lower())]
        for ln in bad:
            fail("log: " + ln.strip())
        if not any(f"Loaded applet {uuid}" in ln for ln in keep):
            log.append("note: no 'Loaded applet' line seen in ~/.xsession-errors delta")
    else:
        log.append("note: ~/.xsession-errors not found; no log capture")

    # 6. probe
    probe_js = (
        "(function(){ const E = imports.ui.extension, AM = imports.ui.appletManager;"
        f" const u = '{uuid}'; const ext = E.getExtension(u); const inst = AM.getRunningInstancesForUuid(u);"
        " return { found: !!ext, state: ext ? ext.meta.state : null,"
        "   stateName: ext ? E.getMetaStateString(ext.meta.state) : null, error: ext ? (ext.meta.error || '') : '',"
        "   version: ext ? ext.meta.version : null, path: ext ? ext.meta.path : null, instances: inst.length,"
        "   instanceIds: inst.map(a => a.instance_id) }; })()"
    )
    try:
        ok, out = ev(probe_js)
        probe = out if ok and isinstance(out, dict) else {"evalError": str(out)}
    except GLib.Error as e:
        probe = {"evalError": e.message}
    r["probe"] = probe
    log.append("probe: " + json.dumps(probe))
    if probe.get("evalError"):
        fail(f"Eval probe failed: {probe['evalError']}")
    else:
        if not probe.get("found"):
            fail("extension not found after reload (Cinnamon did not load it)")
        elif probe.get("state") != 1:
            fail(f"extension state is {probe.get('stateName')} ({probe.get('state')}): {probe.get('error') or 'no message'}")
        if probe.get("instances", 0) < 1:
            fail("no running instance of the applet on any panel")
        if probe.get("path") and Path(probe["path"]).resolve() != target:
            fail(f"Cinnamon loaded the applet from {probe['path']}, not from the repo symlink")
        if probe.get("version") != meta.get("version"):
            fail(f"running version {probe.get('version')} != metadata.json {meta.get('version')}")

    # 7. evals
    run_exprs(opts["evals"], "eval", r["checks"], "post-reload")

    # 8. screenshots
    if opts["screenshot"] and not probe.get("evalError"):
        geo_js = (
            "(function(){ const AM = imports.ui.appletManager, Main = imports.ui.main;"
            f" const inst = AM.getRunningInstancesForUuid('{uuid}'); const out = {{ panels: [], applet: null }};"
            " for (const p of Main.panelManager.panels) { if (!p) continue; const [x, y] = p.actor.get_transformed_position();"
            "   out.panels.push({ id: p.panelId, x: Math.round(x), y: Math.round(y), w: Math.round(p.actor.width), h: Math.round(p.actor.height) }); }"
            " if (inst.length) { const a = inst[0].actor; const [x, y] = a.get_transformed_position();"
            "   out.applet = { x: Math.round(x), y: Math.round(y), w: Math.round(a.width), h: Math.round(a.height), mapped: !!a.mapped, panel: inst[0].panel ? inst[0].panel.panelId : null }; }"
            " return out; })()"
        )
        try:
            ok, out = ev(geo_js)
            geo = out if ok and isinstance(out, dict) else None
        except GLib.Error:
            geo = None
        shots = []
        r["geometry"] = geo
        if geo and geo.get("applet") and not geo["applet"].get("mapped"):
            # Auto-hidden panel: the actor is off-screen (y < 0) and unmapped, so a
            # capture there shows whatever is on screen (the wallpaper), not the applet.
            log.append("screenshot: applet actor is not mapped (panel hidden / auto-hide); the PNGs show the screen at that spot, not the applet")
            say("note: panel is hidden (auto-hide); screenshots will not show the applet")
        if geo and geo.get("applet"):
            a = geo["applet"]
            pan = next((p for p in geo["panels"] if p["id"] == a.get("panel")), None) or (geo["panels"][0] if geo["panels"] else None)
            pad = 8
            targets = [("applet.png", max(0, a["x"] - pad), max(0, a["y"] - pad), a["w"] + 2 * pad, a["h"] + 2 * pad)]
            if pan:
                targets.append(("panel.png", pan["x"], pan["y"], pan["w"], pan["h"]))
            for name, x, y, w, h in targets:
                path = app.out_dir / name
                if path.exists():
                    path.unlink()
                try:
                    call("ScreenshotArea", GLib.Variant("(biiiibs)", [False, x, y, w, h, False, str(path)]))
                except GLib.Error as e:
                    log.append(f"screenshot {name} failed: {e.message}")
                    continue
                for _ in range(20):
                    if path.exists() and path.stat().st_size > 0:
                        break
                    time.sleep(0.25)
                if path.exists():
                    shots.append(str(path.relative_to(GROUP_ROOT)))
                else:
                    log.append(f"screenshot {name} was not written")
        else:
            log.append("screenshot skipped: could not read applet geometry")
        r["screenshots"] = shots

    # 9. restore, then the post-removal evals
    def snapshots_differ() -> bool:
        """True if a settings file or a runtime directory no longer matches its snapshot."""
        now = {f.name: f.read_bytes() for f in spices_dir.glob("*.json")} if spices_dir.is_dir() else None
        if (now or {}) != (settings_before or {}):
            return True
        for rt_dir, existed, backup in runtime_snap:
            if rt_dir.exists() != existed:
                return True
            if existed:
                cmp = filecmp.dircmp(rt_dir, backup)
                pending = [cmp]
                while pending:
                    c = pending.pop()
                    if c.left_only or c.right_only or c.funny_files or \
                            filecmp.cmpfiles(c.left, c.right, c.common_files, shallow=False)[1:] != ([], []):
                        return True
                    pending.extend(c.subdirs.values())
        return False

    def restore_snapshots() -> list[str]:
        """Put the settings files and runtime directories back exactly as found.
        Nothing may be running on them: an instance's teardown flushes pending
        writes over a restored file."""
        restored = []
        if spices_dir.is_dir():
            for f in spices_dir.glob("*.json"):
                if settings_before is None or f.name not in settings_before:
                    f.unlink()
                    restored.append(f"removed {f.name}")
        if settings_before:
            spices_dir.mkdir(parents=True, exist_ok=True)
            for name, data in settings_before.items():
                target = spices_dir / name
                if not target.exists() or target.read_bytes() != data:
                    target.write_bytes(data)
                    restored.append(f"restored {name}")
        if settings_before is None and spices_dir.is_dir() and not any(spices_dir.iterdir()):
            spices_dir.rmdir()
            restored.append("removed the settings directory (did not exist before)")
        for rt_dir, existed, backup in runtime_snap:
            if rt_dir.exists():
                shutil.rmtree(rt_dir)
            if existed:
                shutil.copytree(backup, rt_dir, symlinks=True)
                restored.append(f"restored {rt_dir}")
            else:
                restored.append(f"removed {rt_dir} (did not exist before)")
        return restored

    removed = r["addedToPanel"] and (not opts["keep"] or opts["uninstall"])
    if removed:
        settings.set_strv("enabled-applets", enabled_before)
        Gio.Settings.sync()
        log.append("restored enabled-applets")
        say(f"removed {uuid} from panel {r['panel']} again (use --keep to leave it)")
        if opts["post"]:
            time.sleep(1.5)   # let appletManager unload it before we look
        else:
            time.sleep(1.0)
        # The instance is gone, so nothing watches the files any more.
        restored = restore_snapshots()
        r["settingsRestored"] = restored
        log.append("settings: " + (", ".join(restored) if restored else "unchanged"))
        if restored:
            say("settings/runtime files put back as found: " + ", ".join(restored))
    elif not r["addedToPanel"]:
        # The owner's instance (--use-existing, 2026-10-12): its settings and
        # runtime data are put back too. It must not be running while they
        # change, so ReloadXlet is split in two — unload (deleteConfig false,
        # reload true, as extension.reloadExtension() does), restore, load.
        if snapshots_differ():
            unload_js = (f"(function(){{ const E = imports.ui.extension; E.unloadExtension('{uuid}', E.Type.APPLET, false, true);"
                         f" return !E.getExtension('{uuid}'); }})()")
            load_js = (f"(function(){{ const E = imports.ui.extension; imports.ui.main._addXletDirectoriesToSearchPath();"
                       f" E.loadExtension('{uuid}', E.Type.APPLET); return true; }})()")
            try:
                ok, out = ev(unload_js)
                if not (ok and out is True):
                    fail(f"could not unload the owner's instance to restore its files ({out}); nothing restored")
                else:
                    restored = restore_snapshots()
                    ok, out = ev(load_js)
                    if not (ok and out is True):
                        fail(f"reloading the owner's instance after the restore failed ({out}); ReloadXlet by hand")
                    time.sleep(opts["wait"])
                    ok, n = ev(f"imports.ui.appletManager.getRunningInstancesForUuid('{uuid}').length")
                    if n != len(entries):
                        fail(f"after the restore {n} instance(s) are running, expected {len(entries)}")
                    r["settingsRestored"] = restored
                    log.append("owner's instance unloaded, settings/runtime restored, loaded again: " + ", ".join(restored))
                    say("owner's settings/runtime files put back as found (instance unloaded meanwhile): " + ", ".join(restored))
            except GLib.Error as e:
                fail(f"restoring the owner's instance failed: {e.message}")
        else:
            r["settingsRestored"] = []
            log.append("settings: unchanged (owner's instance)")

    # Post-removal evals prove what removal restored; with no removal there is
    # nothing for them to prove, so they are recorded as skipped.
    if removed:
        run_exprs(opts["post"], "post", r["postChecks"], "post-removal")
    elif opts["post"]:
        why = "this run drove an instance it did not add" if not r["addedToPanel"] else "--keep left the applet on the panel"
        r["postSkipped"] = why
        for expr in opts["post"]:
            r["postChecks"].append({"expr": expr, "ok": None, "skipped": why})
        log.append(f"post: {len(opts['post'])} post-removal eval(s) skipped — {why}")
        say(f"post skipped ({len(opts['post'])}): nothing was removed — {why}")
    if snap_root is not None:
        if r["pass"] or "settingsRestored" in r:
            shutil.rmtree(snap_root, ignore_errors=True)
        else:
            log.append(f"runtime snapshot kept at {snap_root} (the restore did not happen)")
            say(f"runtime snapshot kept at {snap_root}")
    if opts["uninstall"] and link.is_symlink():
        link.unlink()
        log.append(f"removed symlink {link}")

    # 10. Cinnamon still alive? Unloading an applet is when CJS collects its
    #     GObjects; a leaky applet can take the shell down right here
    #     (2026-09-13: SIGABRT in GC after a demo applet was removed).
    time.sleep(3.0)
    try:
        call("GetRunningXletUUIDs", GLib.Variant("(s)", ["applet"]), timeout_ms=5000)
        r["cinnamonAlive"] = True
    except GLib.Error as e:
        r["cinnamonAlive"] = False
        fail(f"Cinnamon is not answering on DBus after the run ({e.message}); check `coredumpctl list` and ~/.xsession-errors")
    r["seconds"] = round(time.time() - t_reload, 1)
    (app.out_dir / "live.log").write_text("\n".join(log) + "\n")


# ---------------------------------------------------------------------------
if opts["tests"]:
    run_tests()
if opts["live"]:
    try:
        run_live()
    except Exception as e:  # noqa: BLE001 — a harness crash is a FAIL, not a traceback-only exit
        failures.append(f"live check crashed: {type(e).__name__}: {e}")
        result["live"]["pass"] = False
        result["live"]["ran"] = True
        result["live"]["crash"] = repr(e)

result["pass"] = not failures and (not opts["tests"] or result["tests"].get("pass")) and (not opts["live"] or result["live"].get("pass"))
result["failures"] = failures
(app.out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
say(("PASS" if result["pass"] else "FAIL") + f" {uuid} v{result['version']} — " +
    ", ".join(p for p, on in (("tests", opts["tests"]), ("live", opts["live"])) if on) +
    f" → {app.out_dir.relative_to(GROUP_ROOT)}/result.json")
for fmsg in failures:
    say("  - " + fmsg)
sys.exit(0 if result["pass"] else 1)
