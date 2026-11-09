# shared/ — canonical shared modules

Modules more than one applet needs, kept once here and **copied** into
each applet that declares them: an installed applet can't depend on
anything outside its own folder, and applets never depend on each other.

## Modules

| Module | What it is | Embedded by |
|---|---|---|
| `Spawn.js` | `run(argv, opts)` → Promise of `{ok, status, stdout, stderr, timedOut, error}` (never throws/rejects for ordinary failures); `available(program)`; `LineReader(argv, {onLine, onExit})` for long-running processes with `start()/stop()` | Switchboard (`rfkill`) |
| `DBusProxy.js` | `DBusProxy.create({bus, name, path, iface})` → Promise; `available`, `get(prop)`, `properties()`, `set(prop, variant)`, `call(method, params)`, `onPropertiesChanged`, `onSignal`, `onOwnerChanged`, `destroy()`. No interface XML needed; values come back `recursiveUnpack`ed | Switchboard, Hush |
| `Dnd.js` | `new Dnd()` over `org.cinnamon.desktop.notifications display-notifications`: `active`, `set(bool)`, `toggle()`, `onChanged(cb)`, `destroy()`. Cinnamon's system-wide Do Not Disturb (2026-09-19) | Hush (reads it as the "set in System Settings" reason), Switchboard (tile) |
| `Inhibitors.js` | `await Inhibitors.create()` over `org.gnome.SessionManager`: `list` of `{path, appId, reason, flags}` kept current via InhibitorAdded/Removed, `find(appIds)`, `onChanged(cb)`, `destroy()`. `org.freedesktop.ScreenSaver` inhibits are invisible here (2026-09-19) | Hush (recording-app trigger), Switchboard (Keep Awake) |
| `PowerProfiles.js` | `await PowerProfiles.create()` over power-profiles-daemon: `available`, `profiles` (power-saver/balanced/performance order), `active`, `holds`, `degraded`, `set(profile)`, `onChanged(cb)`, `destroy()`; pure mappers `profileNames`, `holdList` (2026-10-13) | Switchboard (Power Mode tile and page) |
| `NightLight.js` | `await NightLight.create()` (never rejects): gsettings switch and temperature (`org.cinnamon.settings-daemon.plugins.color`) + csd-color `NightLightActive` / `DisabledUntilTomorrow`; `available`, `enabled`, `active`, `canPause`, `pausedUntilTomorrow`, `temperature`, `untilText()`, setters, `onChanged`, `destroy()`; works key-only without csd-color (2026-10-13) | Switchboard (Night Light tile and page) |

Every module is pure `GLib`/`Gio` (no `imports.ui`), so it runs under
plain `cjs` and is tested headless in `cinnamon-tools/tests/shared/`.

## Convention

- Canonical file: first line `// @shared <Name>.js`, ends with a newline,
  top-level `function`/`class`/`var` declarations are the exports
  (Cinnamon's loader). Load from an applet with
  `require('./modules/shared/<Name>')`; from another shared module with
  the same path (paths are applet-relative even inside `modules/`).
- An applet declares what it embeds in `<applet>/tests/harness.json`:
  `"shared": ["Spawn", "DBusProxy"]` — by hand, or at scaffold time with
  `new_applet.py <name> "<description>" --shared Spawn,DBusProxy`, which
  also runs the sync.
- The copy lands at `files/<uuid>/modules/shared/<Name>.js`, headed by
  `// @shared <Name>.js <sha256-of-canonical-body> — vendored copy, do not edit; …`.
  The body is byte-identical to the canonical file.
- **Never edit a copy.** Fix the canonical file, run `sync_shared.py`,
  bump the version of every applet whose copy changed (it is a change
  under `files/<uuid>/`), and add CHANGELOG entries.
- `check.py` warns (`shared:` rule) when a copy's body differs from the
  canonical file, when its header hash is stale, when a declared module is
  missing, and when `modules/shared/` holds an undeclared or header-less
  file. Warnings, not errors: the copy is what ships and is valid alone.

## Commands

```sh
python3 ../cinnamon-tools/tools/sync_shared.py                  # copy into every applet that declares modules
python3 ../cinnamon-tools/tools/sync_shared.py <uuid>           # one applet
python3 ../cinnamon-tools/tools/sync_shared.py --check          # report only; exit 1 if anything is out of date
python3 ../cinnamon-tools/tools/sync_shared.py --list           # modules and who embeds them
python3 ../cinnamon-tools/harness/run.py shared --tests         # headless suites in cinnamon-tools/tests/shared/
python3 ../cinnamon-tools/harness/run.py shared --tests --suite spawn
```

`run.py shared` stages the canonical files as vendored copies into
`cinnamon-tools/out/shared/staging/modules/shared/` and runs each suite with that
directory as the "applet dir", so tests load modules through
`T.makeRequire(ARGV[0])('./modules/shared/<Name>')` — the same path an
applet uses. `tooling.test.py` there covers `tools/lib/shared.py` itself
(header parsing, sync, drift, staging) on a temporary tree.

## Adding a module

1. Write `cinnamon-tools/shared/<Name>.js` with the header line; pure GLib/Gio;
   no dangerous substrings; trailing newline.
2. Add `cinnamon-tools/tests/shared/<name>.test.js` and run `run.py shared --tests`.
3. Declare it in each applet's `harness.json`, run `sync_shared.py`, bump
   those applets' versions.
4. Add a row to the table above.
