# Cinnamon applet reference — the CJS runtime and its gotchas

Long form of an applet's `AGENTS.md` → "Writing code". Cinnamon-specific rules that
differ from general JS development; violations cause subtle, hard-to-debug
failures. Verified on Cinnamon 6.6.9 (CJS 115, SpiderMonkey 115), Mint 22.3,
2026-09-13 unless a line says otherwise. Add to this file when a rule proves
itself (or fails) on a real applet; date the entry.

---

## 1. The CJS runtime

Cinnamon uses **CJS**, a fork of GJS (GNOME JavaScript) on Mozilla
SpiderMonkey. It is NOT Node.js and NOT a browser.

- **No `require()`** — `imports.gi.*` for GObject libraries, `imports.ui.*`
  for Cinnamon UI modules, `imports.misc.*` for Cinnamon utilities.
- **No npm packages** — only what GObject introspection exposes.
- **No `setTimeout`/`setInterval`** — `GLib.timeout_add()` /
  `GLib.timeout_add_seconds()`; see §6.
- **No `console.log`** — `global.log()`, `global.logWarning()`,
  `global.logError()`.
- **`async`/`await` and Promises work** (checked 2026-09-13 with
  `cjs -c 'async function f(){await Promise.resolve();return 42} f().then(print)'`).
  `Gio._promisify` exists too. Earlier Wallshift docs said "no async/await";
  that was true of Cinnamon 4-era CJS, not 6.6. The main-loop rule still
  applies: an awaited promise must wrap an *async* GLib/Gio call, never a
  blocking one.
- **ES6 classes** are the norm. No `Lang.Class`, no prototype assignment.
- **`const`/`let` for locals; `var` for module exports**: a symbol is visible
  through `imports.modules.<Module>.<symbol>` only when declared with `var`
  at the top level of the module file.
- **Module loading — use `require()`.** Cinnamon evaluates `applet.js`
  through its own loader (`js/misc/fileUtils.js` `requireModule`), which
  passes a `require(path)` function into the module scope. `require('./modules/Utils')`
  loads a sibling file, exports its top-level `var`/`const`/`function`/
  `class` declarations (or `module.exports`), and — the important part —
  is **re-evaluated on `ReloadXlet`** (`unloadModule` drops every cached
  module under the applet's dir). The GJS importer route
  (`imports.searchPath.unshift(metadata.path); imports.modules.Utils`)
  caches each module for the life of the Cinnamon process: after a reload
  the new `applet.js` runs against the *old* modules. Wallshift hit exactly
  this on 2026-09-13 (`normalizePath is not a function` after 0.0.2 added it
  to `Utils.js`). Importer entries are non-configurable — `delete
  imports.modules` and `delete imports.modules.Utils` both return `false` —
  but the root importer's properties are *writable*, and Cinnamon deletes
  `imports.applets[<uuid>]` on every unload, so a fresh sub-importer exists
  there: `imports.modules = imports.applets[uuid].modules` swaps the stale
  one out (verified 2026-09-13). The harness does this before every reload
  for applets that use the pattern; new code should use `require()` and not
  need it. Inside a `require()`d module, `require` and `__dirname` are
  available too, so modules can require each other.
- **Byte → string**: `new TextDecoder().decode(bytes)`;
  `imports.byteArray.toString()` still exists as a fallback.

## 2. Essential imports

```javascript
const Applet    = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const Settings  = imports.ui.settings;
const Main      = imports.ui.main;
const GLib      = imports.gi.GLib;
const Gio       = imports.gi.Gio;
const St        = imports.gi.St;         // widgets
const Clutter   = imports.gi.Clutter;    // events / actors
const Tweener   = imports.ui.tweener;    // animations
const Gettext   = imports.gettext;       // translations
```

## 3. Applet base classes and lifecycle

`imports.ui.applet` (Cinnamon 6.6, `js/ui/applet.js`):

- `Applet.Applet` — empty panel actor; you add children.
- `Applet.IconApplet` — an icon (`set_applet_icon_name`,
  `set_applet_icon_symbolic_name`, `set_applet_icon_path`).
- `Applet.TextApplet` — a label (`set_applet_label`).
- `Applet.TextIconApplet` — both.

Hooks Cinnamon calls (override what you need):

| Hook | When |
|---|---|
| `constructor(metadata, orientation, panelHeight, instanceId)` | `main()` constructs it |
| `on_applet_added_to_panel(userEnabled)` | after the actor is on the panel |
| `on_applet_clicked(event)` / `on_applet_middle_clicked(event)` | clicks |
| `on_applet_removed_from_panel(deleteConfig)` | removal **and reload** — release everything here |
| `on_applet_reloaded(deleteConfig)` | reload only (after removal) |
| `on_orientation_changed(orientation)` | panel moved top/bottom/left/right |
| `on_panel_height_changed()` / `on_panel_icon_size_changed()` | panel resized |
| `on_applet_instances_changed()` | another instance added/removed |
| `finalizeContextMenu()` | build extra right-click items |
| `configureApplet(tab)` | "Configure…" — default opens the schema dialog |

Do not name your own cleanup `destroy()` and rely on it being called;
Cinnamon calls `on_applet_removed_from_panel`.

**Re-adding is not reloading (2026-09-19).** Removing an applet's last
instance from a panel does not unload its module (`appletManager` calls
`removeAppletFromPanels`, never `Extension.unloadExtension`), and for
`max-instances: 1` applets the settings file is kept too. Re-adding the
applet from System Settings therefore runs the code that was loaded earlier
in the session. After changing files use `ReloadXlet` (the harness does) or
restart Cinnamon.

## 4. The `metadata` object

First constructor argument: `metadata.uuid`, `metadata.path` (absolute
install dir — always use it for files inside the applet), `metadata.name`,
`metadata.version`. Cinnamon adds `metadata.state`, `metadata.error`.

## 5. Signal management

**Every signal connection is stored and disconnected on removal.**

```javascript
this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._onMonitorsChanged());
// on_applet_removed_from_panel:
if (this._monitorsId) { Main.layoutManager.disconnect(this._monitorsId); this._monitorsId = 0; }
```

The same applies to `Gio.FileMonitor` (`.cancel()`), `St.Adjustment`
`notify::value` handlers, DBus proxies, and chrome actors registered with
`Main.layoutManager.addChrome()` (remove with `removeChrome()` then
`destroy()`). A leak here makes Cinnamon call methods on dead objects.

## 6. Timer management

```javascript
this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
    this._onTimer();
    return GLib.SOURCE_CONTINUE;   // or GLib.SOURCE_REMOVE to stop
});
// on_applet_removed_from_panel:
if (this._timerId) { GLib.source_remove(this._timerId); this._timerId = 0; }
```

`Mainloop.timeout_add` (`imports.mainloop`) is the older spelling of the
same thing; pick one.

## 7. Subprocesses (async only)

Never `GLib.spawn_command_line_sync()` / `GLib.spawn_sync()` — they block
the shell. Use `Gio.Subprocess`:

```javascript
function spawnAsync(argv, callback) {
    try {
        let proc = new Gio.Subprocess({ argv, flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE });
        proc.init(null);
        proc.communicate_utf8_async(null, null, (p, res) => {
            try {
                let [ok, stdout, stderr] = p.communicate_utf8_finish(res);
                callback(null, stdout, stderr, p.get_exit_status());
            } catch (e) { callback(e, null, null, -1); }
        });
    } catch (e) { callback(e, null, null, -1); }
}
```

Fire-and-forget launches (`xdg-open`, a settings dialog) may use
`GLib.spawn_command_line_async()`.

**Cinnamon's "dangerous xlet" scan.** *System Settings → Applets* runs a
literal substring search over every `.js` file
(`cinnamon-settings/bin/ExtensionCore.py`, `UNSAFE_ITEMS`) and shows *"This
applet contains function calls that could potentially cause Cinnamon to
crash or freeze"* on any hit. The list on 6.6.9: `spawn_sync`,
`spawn_command_line_sync`, `GTop`, `get_file_contents_utf8_sync`. Because it
is a substring match, even a comment triggers it — none of the four may
appear anywhere in shipped JS. `check.py` errors on them.

## 8. File I/O

Small JSON files (a few KB) may be read/written synchronously —
`GLib.file_get_contents` / `GLib.file_set_contents` take well under a
millisecond. Debounce bursts of writes. Anything larger, or on a network
path, goes through `Gio.File` async methods (`load_contents_async`,
`replace_contents_async`).

```javascript
let [ok, contents] = GLib.file_get_contents(path);
let text = new TextDecoder().decode(contents);
GLib.file_set_contents(path, JSON.stringify(obj, null, 2));
```

`Gio.File.copy()` has been observed to silently no-op in Cinnamon's CJS
binding (Wallshift, 2026-09); read + write instead when a copy matters.

## 9. Settings (AppletSettings)

All user settings live in `settings-schema.json` and are bound via
`Settings.AppletSettings`:

```javascript
this._settings = new Settings.AppletSettings(this, UUID, instanceId);
this._settings.bind('interval-minutes', '_intervalMinutes', () => this._rebuildTimer());
// on removal:
this._settings.finalize();
```

Cinnamon stores values in
`~/.config/cinnamon/spices/<uuid>/<instanceId>.json` (single-instance
applets: `<uuid>.json`) and fires the callback when the file changes. A
custom dialog that writes that file directly should then call
`org.Cinnamon.updateSetting` over DBus so the callback fires immediately.

## 10. Panel menu

```javascript
this._menuManager = new PopupMenu.PopupMenuManager(this);
this._menu = new Applet.AppletPopupMenu(this, orientation);
this._menuManager.addMenu(this._menu);
this._menu.addAction(_('Label'), () => { /* ... */ });
this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
```

`this._menu.toggle()` in `on_applet_clicked()`. Right-click items:
`this._applet_context_menu.addMenuItem(...)`.

## 11. Translations

```javascript
const Gettext = imports.gettext;
Gettext.bindtextdomain(UUID, GLib.get_home_dir() + '/.local/share/locale');
function _(str) { let t = Gettext.dgettext(UUID, str); return t !== str ? t : window._(str); }
```

Wrap every user-visible string in `_()`. `cinnamon-xlet-makepot` generates
`files/<uuid>/po/<uuid>.pot`; translators add `<lang>.po`; never ship `.mo`.

## 12. Custom settings dialogs

The right-click "Configure…" item calls `this.configureApplet()`. Override it
only for a genuinely custom dialog, and spawn with the explicit interpreter:

```javascript
configureApplet() {
    GLib.spawn_command_line_async(`/usr/bin/python3 ${this._metadata.path}/configure.py ${UUID} ${this._instanceId}`);
}
```

`python3` on `PATH` may resolve to an AppImage Python without `gi`. The
dialog process writes Cinnamon's settings file (§9) and calls
`org.Cinnamon.updateSetting(uuid, instanceKey, key, jsonValue)`.

## 13. Known GDK/GJS gotchas in Cinnamon

- **`monitors-changed` lives on `Main.layoutManager`**, not `global.display`
  (which throws "No signal 'monitors-changed'").
- **`Gdk.Monitor.get_connector()`** may not be bound in every build; wrap in
  `try/catch` and fall back to `Gdk.Screen.get_monitor_plug_name(i)`.
- **DRM vs. GDK connector naming**: sysfs uses `HDMI-A-1`, GDK/xrandr use
  `HDMI-1`. Try both when reading EDID from sysfs.
- **`CinnamonDesktop.RRScreen.new()` blocks** for tens of seconds doing
  RandR queries. Do not use it; GDK + sysfs is fine.
- **ImageMagick 6 `-gravity` persists inside `( )`**; reset it before every
  `-geometry -composite`.
- **Cinnamon redraws the background only when `picture-uri` changes**;
  alternate two output filenames if you set the same wallpaper twice.
- **AppletSettings `filechooser` may return a `file://` URI**; normalise
  before use.
- **Overlays**: register actors with `Main.layoutManager.addChrome(actor)`
  for correct event routing and z-order; `global.display.get_monitor_work_area(idx)`
  gives the area above a bottom panel.
- **`cinnamon-version`** in `metadata.json` is a minimum (`extension.js`
  `versionCheck`): the xlet loads if the running major.minor is ≥ any listed
  entry. `["6.6"]` therefore covers every later release.
- **Animations (checked 2026-09-20, 6.6.9)**: `actor.ease({ x, opacity,
  duration, mode, delay, onComplete, onStopped })` is Cinnamon's patched
  Clutter helper (`ui/environment.js`); `onComplete` fires only when the
  transition finished, `onStopped(finished)` always. Durations pass through
  `St.Settings.animations_enabled`, which `main.js` sets from
  `org.cinnamon desktop-effects-workspace` — the **Effects master switch** —
  so with it off every `ease()` completes at once (0 ms, callbacks
  synchronous, no transition object). Cinnamon's own menus additionally
  gate on `Main.wm.desktop_effects_menus` (master && "Effects on menus");
  applet UI should follow the same flag and apply values directly when it
  is false (`GObject.set({ x, opacity, translation_x })` takes underscore
  names). Destroying an actor mid-transition stops it (`onStopped(false)`),
  so cleanup callbacks must tolerate an already-destroyed actor.
- **St CSS has no `opacity`** (`.foo { opacity: 0.5 }` is silently ignored,
  although Mint themes contain it); dim with `actor.opacity` instead.
  St does support `box-shadow`, `text-shadow` and `transition-duration`
  (crossfades a widget's paint state on any style change — `:hover`,
  `:active`, a class toggle). Colours: give a drawer/popup the theme's
  `menu` class to inherit background, text colour and shadow, then add
  only translucent mid-grey emphasis so it reads on light and dark themes.
- **`Main.pushModal()` can be refused** (`pushModal: invocation of
  begin_modal failed`) whenever another X grab is active — a menu open, a
  button held, a drag. Treat a `false` return as "open without the grab",
  never as an error, and do not assert on `_modal` in timing-sensitive
  checks.
- **Animated height**: `actor.set_height(h)` pins min and natural alike
  (safe inside Cinnamon's min-height scrolling `St.BoxLayout`),
  `set_height(-1)` unpins; measure with `get_preferred_height(width)`
  *after* parenting (an unparented St widget has no theme node) and clip
  with `set_clip_to_allocation(true)` while it grows or shrinks.

## 14. Testing outside Cinnamon

Modules that only use `imports.gi.*` (GLib, Gio, Gdk) run under plain `cjs`
through `T.makeRequire(appletDir)` from `harness/lib.js`, a mirror of
Cinnamon's loader (strict mode, regex exports, per-path cache, applet-dir
relative paths), so a test sees what the shell will see. Modules that touch `imports.ui.*`, `St` or
`Clutter` need the live session: `run.py --live`. A parse-only check for
any file is `new Function(source)` under `cjs`, which is what `check.py`
does.
