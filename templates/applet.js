// applet.js — __NAME__: __DESCRIPTION__
//
// Structure (see cinnamon-tools/docs/cinnamon-reference.md for the reasoning):
//   constructor                     — panel widget, settings bindings, menu
//   on_applet_clicked               — toggle the menu
//   on_applet_removed_from_panel    — release everything (also runs on reload)
//   main                            — Cinnamon's entry point

const Applet    = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const Settings  = imports.ui.settings;
const Main      = imports.ui.main;
const GLib      = imports.gi.GLib;
const Gio       = imports.gi.Gio;
const Gettext   = imports.gettext;

// Local modules go in modules/ and are loaded with Cinnamon's own require(),
// paths relative to the applet directory (also from inside modules/):
//   const Utils = require('./modules/Utils');
// Never imports.searchPath / imports.modules — those are cached across reloads.

const UUID = '__UUID__';
const LOG_PREFIX = '[__SHORTNAME__] ';

Gettext.bindtextdomain(UUID, GLib.get_home_dir() + '/.local/share/locale');
function _(str) {
    const translated = Gettext.dgettext(UUID, str);
    return translated !== str ? translated : window._(str);
}

class __CLASS__ extends Applet.IconApplet {

    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);

        this._metadata   = metadata;
        this._instanceId = instanceId;
        this._timerId    = 0;      // every GLib source id lives on `this` so removal can clear it
        this._signals    = [];     // [obj, id] pairs to disconnect on removal

        this.set_applet_icon_symbolic_name('cinnamon-symbolic');
        this.set_applet_tooltip(_('__NAME__'));

        // Settings — every key in settings-schema.json is bound here.
        this._settings = new Settings.AppletSettings(this, UUID, instanceId);
        this._settings.bind('enabled', '_enabled', () => this._onSettingsChanged());

        // Panel menu.
        this._menuManager = new PopupMenu.PopupMenuManager(this);
        this._menu = new Applet.AppletPopupMenu(this, orientation);
        this._menuManager.addMenu(this._menu);
        this._menu.addAction(_('Do something'), () => this._doSomething());

        this._onSettingsChanged();
    }

    _onSettingsChanged() {
        // React to bound settings; must be safe to call any number of times.
        this._restartTimer();
    }

    _restartTimer() {
        this._stopTimer();
        if (!this._enabled) return;
        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => {
            this._tick();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopTimer() {
        if (this._timerId) {
            GLib.source_remove(this._timerId);
            this._timerId = 0;
        }
    }

    _tick() {
        // Periodic work. Nothing here may block: shell out with Gio.Subprocess
        // and communicate_utf8_async, read files with Gio.File async APIs.
    }

    _doSomething() {
        global.log(LOG_PREFIX + 'menu action');
    }

    on_applet_clicked(event) {
        this._menu.toggle();
    }

    on_applet_removed_from_panel(deleteConfig) {
        // Cinnamon calls this on removal AND on reload. Release everything.
        this._stopTimer();
        for (const [obj, id] of this._signals) obj.disconnect(id);
        this._signals = [];
        if (this._menu) { this._menu.destroy(); this._menu = null; }
        if (this._settings) { this._settings.finalize(); this._settings = null; }
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new __CLASS__(metadata, orientation, panelHeight, instanceId);
}
