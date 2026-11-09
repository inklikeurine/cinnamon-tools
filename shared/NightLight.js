// @shared NightLight.js
//
// Cinnamon's Night Light: the on/off switch and colour temperature live in
// gsettings (org.cinnamon.settings-daemon.plugins.color — what System
// Settings and nightlight@cinnamon.org write); whether it is shining right
// now and "disabled until tomorrow" live on csd-color
// (org.cinnamon.SettingsDaemon.Color, session bus). Pure Gio (on top of the
// shared DBusProxy): runs under plain `cjs`. Works without csd-color (key
// only) and reports available === false when the schema is not installed.
//
//   const NightLight = require('./modules/shared/NightLight');
//   const nl = await NightLight.NightLight.create();   // never rejects
//   nl.available            → the schema exists
//   nl.enabled              → the switch (gsettings night-light-enabled)
//   nl.setEnabled(bool)
//   nl.active               → shining now (csd NightLightActive; falls back to enabled)
//   nl.pausedUntilTomorrow  → csd DisabledUntilTomorrow
//   nl.canPause             → csd-color is running (pausing needs it)
//   await nl.setPausedUntilTomorrow(bool)               // needs csd-color
//   nl.temperature          → kelvin (gsettings night-light-temperature)
//   nl.setTemperature(k)    → clamped to MIN_K..MAX_K
//   nl.untilText()          → '06:00' when the schedule says when it ends, else ''
//   nl.onChanged(() => …) → handler id; nl.disconnect(id); nl.destroy()

const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;

const DBusProxy = require('./modules/shared/DBusProxy').DBusProxy;

var SCHEMA = 'org.cinnamon.settings-daemon.plugins.color';
var KEY_ENABLED = 'night-light-enabled';
var KEY_TEMPERATURE = 'night-light-temperature';
var KEY_MODE = 'night-light-schedule-mode';
var KEY_TO = 'night-light-schedule-to';
var COLOR_NAME = 'org.cinnamon.SettingsDaemon.Color';
var COLOR_PATH = '/org/cinnamon/SettingsDaemon/Color';
var COLOR_IFACE = 'org.cinnamon.SettingsDaemon.Color';
var MIN_K = 1700;   // the range cs_nightlight.py offers
var MAX_K = 6500;

// 6.5 → '06:30'. Anything that is not a finite hour in [0, 24) → ''.
function formatHours(h) {
    if (typeof h !== 'number' || !isFinite(h) || h < 0 || h >= 24) return '';
    let mins = Math.round(h * 60);
    if (mins >= 24 * 60) mins = 0;
    const pad = n => (n < 10 ? '0' : '') + n;
    return pad(Math.floor(mins / 60)) + ':' + pad(mins % 60);
}

// When does the current Night Light period end? mode is the schedule mode
// string ('auto' follows sunrise, 'manual' uses the "to" hour, anything else
// — 'always' — never ends).
function untilHours(mode, toHour, sunriseHour) {
    if (mode === 'manual') return toHour;
    if (mode === 'auto') return sunriseHour;
    return NaN;
}

function _schemaFor(id) {
    const source = Gio.SettingsSchemaSource.get_default();
    return source ? source.lookup(id, true) : null;
}

class NightLight {
    static create(opts) {
        const o = opts || {};
        const schema = _schemaFor(SCHEMA);
        const settings = o.settings || (schema ? new Gio.Settings({ schema_id: SCHEMA }) : null);
        if (o.noDaemon) return Promise.resolve(new NightLight(settings, null));
        return DBusProxy.create({ bus: 'session', name: COLOR_NAME, path: COLOR_PATH, iface: COLOR_IFACE,
                                  flags: Gio.DBusProxyFlags.DO_NOT_AUTO_START })
            .then(proxy => new NightLight(settings, proxy), () => new NightLight(settings, null));
    }

    constructor(settings, proxy) {
        this._settings = settings;
        this._proxy = proxy;
        this._handlers = new Map();
        this._nextId = 1;
        this._destroyed = false;
        this._settingsIds = [];
        if (this._settings) {
            for (const k of [KEY_ENABLED, KEY_TEMPERATURE, KEY_MODE, KEY_TO])
                if (this._hasKey(k)) this._settingsIds.push(this._settings.connect('changed::' + k, () => this._emit()));
        }
        if (this._proxy) {
            this._proxy.onPropertiesChanged(() => this._emit());
            this._proxy.onOwnerChanged(() => this._emit());
        }
    }

    _hasKey(k) {
        return !!this._settings && this._settings.settings_schema.has_key(k);
    }

    get _daemon() {
        return !this._destroyed && this._proxy !== null && this._proxy.available;
    }

    get available() {
        return !this._destroyed && this._settings !== null;
    }

    get enabled() {
        return this.available && this._settings.get_boolean(KEY_ENABLED);
    }

    setEnabled(on) {
        if (!this.available) return false;
        if (this.enabled === !!on) return false;
        this._settings.set_boolean(KEY_ENABLED, !!on);
        return true;
    }

    get active() {
        if (!this.available) return false;
        if (this._daemon) {
            const a = this._proxy.get('NightLightActive');
            if (typeof a === 'boolean') return a;
        }
        return this.enabled;
    }

    get canPause() {
        return this._daemon;
    }

    get pausedUntilTomorrow() {
        return this._daemon && this._proxy.get('DisabledUntilTomorrow') === true;
    }

    setPausedUntilTomorrow(on) {
        if (!this._daemon) return Promise.reject(new Error('csd-color is not running'));
        return this._proxy.set('DisabledUntilTomorrow', GLib.Variant.new_boolean(!!on));
    }

    get temperature() {
        if (!this.available || !this._hasKey(KEY_TEMPERATURE)) return 0;
        return this._settings.get_uint(KEY_TEMPERATURE);
    }

    setTemperature(kelvin) {
        if (!this.available || !this._hasKey(KEY_TEMPERATURE)) return false;
        const k = Math.round(Math.min(MAX_K, Math.max(MIN_K, Number(kelvin) || MIN_K)));
        if (this.temperature === k) return false;
        this._settings.set_uint(KEY_TEMPERATURE, k);
        return true;
    }

    untilText() {
        if (!this.available || !this._hasKey(KEY_MODE)) return '';
        let mode = '';
        try { mode = this._settings.get_string(KEY_MODE); } catch (e) { return ''; }
        const to = this._hasKey(KEY_TO) ? this._settings.get_double(KEY_TO) : NaN;
        const sunrise = this._daemon ? this._proxy.get('Sunrise') : NaN;
        return formatHours(untilHours(mode, to, sunrise));
    }

    onChanged(cb) {
        if (this._destroyed) return 0;
        const id = this._nextId++;
        this._handlers.set(id, cb);
        return id;
    }

    disconnect(id) {
        this._handlers.delete(id);
    }

    _emit() {
        if (this._destroyed) return;
        for (const cb of Array.from(this._handlers.values())) {
            try { cb(); } catch (e) { /* a listener's problem */ }
        }
    }

    destroy() {
        if (this._destroyed) return;
        this._destroyed = true;
        this._handlers.clear();
        if (this._settings) for (const id of this._settingsIds) this._settings.disconnect(id);
        this._settingsIds = [];
        this._settings = null;
        if (this._proxy) { this._proxy.destroy(); this._proxy = null; }
    }
}
