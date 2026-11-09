// @shared PowerProfiles.js
//
// power-profiles-daemon (org.freedesktop.UPower.PowerProfiles on the system
// bus): which profiles this machine offers, which one is active, who holds
// one, and switching. Pure Gio (on top of the shared DBusProxy): runs under
// plain `cjs`. Switching needs no password for the seat user (polkit
// `switch-profile` is allow_active=yes on Mint 22.3).
//
//   const PowerProfiles = require('./modules/shared/PowerProfiles');
//   const pp = await PowerProfiles.PowerProfiles.create();  // rejects only if the system bus is unreachable
//   pp.available     → the daemon is running and offers at least one profile
//   pp.profiles      → ['power-saver', 'balanced', ('performance')], in that order
//   pp.active        → 'balanced'
//   pp.holds         → [{ appId, profile, reason }]  (apps that asked for a profile)
//   pp.degraded      → '' or the reason performance is degraded
//   await pp.set('power-saver')
//   pp.onChanged(() => …) → handler id; fires on any property change and when the daemon comes or goes
//   pp.disconnect(id); pp.destroy()
//
// A desktop without a CPU/platform driver lists only power-saver and
// balanced (driver "placeholder"); performance appears with a real driver.

const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;

const DBusProxy = require('./modules/shared/DBusProxy').DBusProxy;

var NAME = 'org.freedesktop.UPower.PowerProfiles';
var PATH = '/org/freedesktop/UPower/PowerProfiles';
var IFACE = 'org.freedesktop.UPower.PowerProfiles';
var ORDER = ['power-saver', 'balanced', 'performance'];

// The daemon's Profiles property (aa{sv}, unpacked) → known profile names in ORDER.
function profileNames(profilesProp) {
    const have = new Set((Array.isArray(profilesProp) ? profilesProp : []).map(p => p && p.Profile).filter(Boolean));
    return ORDER.filter(n => have.has(n));
}

// ActiveProfileHolds (aa{sv}, unpacked) → [{ appId, profile, reason }].
function holdList(holdsProp) {
    return (Array.isArray(holdsProp) ? holdsProp : []).map(h => ({
        appId: String((h && h.ApplicationId) || ''),
        profile: String((h && h.Profile) || ''),
        reason: String((h && h.Reason) || '')
    }));
}

class PowerProfiles {
    static create() {
        return DBusProxy.create({ bus: 'system', name: NAME, path: PATH, iface: IFACE })
            .then(proxy => new PowerProfiles(proxy));
    }

    constructor(proxy) {
        this._proxy = proxy;
        this._handlers = new Map();
        this._nextId = 1;
        this._destroyed = false;
        this._proxy.onPropertiesChanged(() => this._emit());
        this._proxy.onOwnerChanged(() => this._emit());
    }

    get available() {
        return !this._destroyed && this._proxy.available && this.profiles.length > 0;
    }

    get profiles() {
        if (this._destroyed) return [];
        return profileNames(this._proxy.get('Profiles'));
    }

    get active() {
        if (this._destroyed) return '';
        return String(this._proxy.get('ActiveProfile') || '');
    }

    get holds() {
        if (this._destroyed) return [];
        return holdList(this._proxy.get('ActiveProfileHolds'));
    }

    get degraded() {
        if (this._destroyed) return '';
        return String(this._proxy.get('PerformanceDegraded') || '');
    }

    set(profile) {
        if (this._destroyed) return Promise.reject(new Error('PowerProfiles destroyed'));
        if (!this.profiles.includes(profile)) return Promise.reject(new Error('unknown profile ' + profile));
        return this._proxy.set('ActiveProfile', GLib.Variant.new_string(profile));
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
        this._proxy.destroy();
    }
}
