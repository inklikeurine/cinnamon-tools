// @shared Inhibitors.js
//
// The session's inhibitor list (org.gnome.SessionManager): what
// inhibit@cinnamon.org adds, what portal-using apps such as OBS add while
// recording, what a video player adds to keep the screen on. Kept current
// through InhibitorAdded / InhibitorRemoved; every entry is resolved to
// { path, appId, reason, flags }. Pure Gio (on top of the shared DBusProxy):
// runs under plain `cjs`. org.freedesktop.ScreenSaver inhibits (Chromium,
// Electron) do NOT appear here — see .dev/notes/system-services.md §8.2.
//
//   const Inhibitors = require('./modules/shared/Inhibitors');
//   const inh = await Inhibitors.Inhibitors.create();   // rejects only if the bus is unreachable
//   inh.available                → the session manager owns its name
//   inh.list                     → [{ path, appId, reason, flags }], current
//   inh.find(appIds)             → first entry whose appId matches one of the ids (case-insensitive), or null
//   inh.onChanged(list => …)     → handler id; fires after every add/remove once the entry is resolved
//   inh.disconnect(id); inh.destroy()
//
// Flags (GNOME): 1 logout, 2 switch user, 4 suspend, 8 idle.

const Gio  = imports.gi.Gio;

const DBusProxy = require('./modules/shared/DBusProxy').DBusProxy;

var NAME = 'org.gnome.SessionManager';
var PATH = '/org/gnome/SessionManager';
var IFACE = 'org.gnome.SessionManager';
var INHIBITOR_IFACE = 'org.gnome.SessionManager.Inhibitor';

var Flags = Object.freeze({ LOGOUT: 1, SWITCH_USER: 2, SUSPEND: 4, IDLE: 8 });

function _call(bus, path, method) {
    return new Promise(resolve => {
        bus.call(NAME, path, INHIBITOR_IFACE, method, null, null, Gio.DBusCallFlags.NONE, 3000, null, (c, res) => {
            try { resolve(c.call_finish(res).deep_unpack()[0]); } catch (e) { resolve(null); }
        });
    });
}

class Inhibitors {
    static create(opts) {
        const o = opts || {};
        const bus = o.bus || Gio.DBus.session;
        return DBusProxy.create({ bus: 'session', name: NAME, path: PATH, iface: IFACE, flags: Gio.DBusProxyFlags.DO_NOT_AUTO_START })
            .then(proxy => {
                const inh = new Inhibitors(proxy, bus);
                return inh._refresh().then(() => inh);
            });
    }

    constructor(proxy, bus) {
        this._proxy = proxy;
        this._bus = bus;
        this._entries = new Map();     // path → { path, appId, reason, flags }
        this._handlers = new Map();    // id → callback
        this._nextId = 1;
        this._destroyed = false;
        this._generation = 0;
        this._proxy.onSignal('InhibitorAdded', ([path]) => this._added(path));
        this._proxy.onSignal('InhibitorRemoved', ([path]) => this._removed(path));
        this._proxy.onOwnerChanged(available => { if (available) this._refresh(); else this._clear(); });
    }

    get available() {
        return !this._destroyed && this._proxy.available;
    }

    get list() {
        return Array.from(this._entries.values());
    }

    find(appIds) {
        const wanted = (Array.isArray(appIds) ? appIds : [appIds]).map(a => String(a || '').toLowerCase()).filter(Boolean);
        for (const e of this._entries.values())
            if (wanted.includes(String(e.appId || '').toLowerCase())) return e;
        return null;
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
        const list = this.list;
        for (const cb of Array.from(this._handlers.values())) {
            try { cb(list); } catch (e) { /* a listener's problem */ }
        }
    }

    _resolve(path) {
        return Promise.all([_call(this._bus, path, 'GetAppId'), _call(this._bus, path, 'GetReason'), _call(this._bus, path, 'GetFlags')])
            .then(([appId, reason, flags]) => ({ path, appId: appId === null ? '' : String(appId), reason: reason === null ? '' : String(reason), flags: flags === null ? 0 : (flags | 0) }));
    }

    _refresh() {
        if (this._destroyed || !this._proxy.available) { this._clear(); return Promise.resolve(); }
        const gen = ++this._generation;
        return this._proxy.call('GetInhibitors').then(([paths]) => {
            return Promise.all((paths || []).map(p => this._resolve(p))).then(entries => {
                if (this._destroyed || gen !== this._generation) return;
                this._entries.clear();
                for (const e of entries) this._entries.set(e.path, e);
                this._emit();
            });
        }, () => { /* service gone between checks */ });
    }

    _added(path) {
        if (this._destroyed) return;
        this._resolve(path).then(entry => {
            if (this._destroyed) return;
            this._entries.set(path, entry);
            this._emit();
        });
    }

    _removed(path) {
        if (this._destroyed) return;
        if (this._entries.delete(path)) this._emit();
    }

    _clear() {
        if (this._entries.size === 0) return;
        this._entries.clear();
        this._emit();
    }

    destroy() {
        if (this._destroyed) return;
        this._destroyed = true;
        this._handlers.clear();
        this._entries.clear();
        this._proxy.destroy();
    }
}
