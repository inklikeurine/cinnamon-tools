// @shared DBusProxy.js
//
// A small wrapper around Gio.DBusProxy for Cinnamon applets: async
// construction, cached properties as plain JS values, method calls as
// Promises, signal tracking, and one destroy() that releases everything.
// No interface XML is needed — GDBus caches every property of the remote
// interface at construction (a GetAll) and keeps them current through
// PropertiesChanged. Pure Gio/GLib: runs under plain `cjs` too.
//
//   const DBusProxy = require('./modules/shared/DBusProxy');
//
//   const ppd = await DBusProxy.create({
//       bus: 'system',                                   // or 'session'
//       name: 'org.freedesktop.UPower.PowerProfiles',
//       path: '/org/freedesktop/UPower/PowerProfiles',
//       iface: 'org.freedesktop.UPower.PowerProfiles',
//   });                     // rejects only when the bus itself is unreachable
//   ppd.available          // true iff the well-known name has an owner right now
//   ppd.get('ActiveProfile')                             // unpacked value, or undefined
//   await ppd.set('ActiveProfile', GLib.Variant.new_string('balanced'))
//   const [names] = await ppd.call('ListNames')          // result tuple, unpacked
//   ppd.onPropertiesChanged((changed, invalidated) => …) // changed = {name: value}
//   ppd.onSignal('ProfileReleased', ([cookie]) => …)
//   ppd.onOwnerChanged(available => …)                   // service came or went
//   ppd.destroy()                                        // disconnect all, drop the proxy
//
// Every on*() returns an id you may pass to disconnect(id); destroy() drops
// them all, so applets usually just call destroy() from
// on_applet_removed_from_panel(). Methods called after destroy() reject /
// return undefined instead of touching a dead proxy.

const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;

function _busType(bus) {
    return bus === 'system' ? Gio.BusType.SYSTEM : Gio.BusType.SESSION;
}

// recursiveUnpack (CJS 115) also opens the 'v' boxes inside a{sv} / aa{sv},
// which is what callers want for property bags like ppd's Profiles.
function _unpack(variant) {
    if (variant === null || variant === undefined) return undefined;
    return typeof variant.recursiveUnpack === 'function' ? variant.recursiveUnpack() : variant.deepUnpack();
}

class DBusProxy {
    static create(spec) {
        return new Promise((resolve, reject) => {
            // GDBus only asserts (and never calls back) on malformed arguments; check first.
            if (!spec || !Gio.dbus_is_name(String(spec.name || '')))
                { reject(new Error('DBusProxy.create: invalid bus name ' + JSON.stringify(spec && spec.name))); return; }
            if (!GLib.variant_is_object_path(String(spec.path || '')))
                { reject(new Error('DBusProxy.create: invalid object path ' + JSON.stringify(spec.path))); return; }
            if (!Gio.dbus_is_interface_name(String(spec.iface || '')))
                { reject(new Error('DBusProxy.create: invalid interface name ' + JSON.stringify(spec.iface))); return; }
            const flags = spec.flags !== undefined ? spec.flags : Gio.DBusProxyFlags.NONE;
            Gio.DBusProxy.new_for_bus(_busType(spec.bus), flags, null, spec.name, spec.path, spec.iface,
                spec.cancellable || null, (obj, res) => {
                    try {
                        resolve(new DBusProxy(Gio.DBusProxy.new_for_bus_finish(res), spec));
                    } catch (e) {
                        reject(e);
                    }
                });
        });
    }

    constructor(proxy, spec) {
        this._proxy = proxy;
        this.spec = { bus: spec.bus, name: spec.name, path: spec.path, iface: spec.iface };
        this._handlers = new Map();   // id → { obj, sid }
        this._nextId = 1;
        this.timeoutMs = spec.timeoutMs || 15000;
    }

    get available() {
        return this._proxy !== null && this._proxy.get_name_owner() !== null;
    }

    get(prop) {
        if (!this._proxy) return undefined;
        return _unpack(this._proxy.get_cached_property(prop));
    }

    properties() {
        if (!this._proxy) return {};
        const out = {};
        for (const name of this._proxy.get_cached_property_names() || [])
            out[name] = _unpack(this._proxy.get_cached_property(name));
        return out;
    }

    set(prop, variant) {
        return this.call('org.freedesktop.DBus.Properties.Set',
            new GLib.Variant('(ssv)', [this.spec.iface, prop, variant]));
    }

    call(method, params, timeoutMs) {
        return new Promise((resolve, reject) => {
            if (!this._proxy) { reject(new Error('DBusProxy destroyed')); return; }
            this._proxy.call(method, params || null, Gio.DBusCallFlags.NONE, timeoutMs || this.timeoutMs, null, (p, res) => {
                try {
                    resolve(_unpack(p.call_finish(res)));
                } catch (e) {
                    reject(e);
                }
            });
        });
    }

    onPropertiesChanged(cb) {
        return this._connect('g-properties-changed', (proxy, changed, invalidated) => {
            cb(_unpack(changed) || {}, invalidated || []);
        });
    }

    onSignal(signalName, cb) {
        return this._connect('g-signal', (proxy, sender, name, params) => {
            if (name === signalName) cb(_unpack(params) || []);
        });
    }

    onOwnerChanged(cb) {
        return this._connect('notify::g-name-owner', () => cb(this.available));
    }

    _connect(signal, handler) {
        if (!this._proxy) return 0;
        const id = this._nextId++;
        this._handlers.set(id, { obj: this._proxy, sid: this._proxy.connect(signal, handler) });
        return id;
    }

    disconnect(id) {
        const h = this._handlers.get(id);
        if (!h) return;
        this._handlers.delete(id);
        h.obj.disconnect(h.sid);
    }

    destroy() {
        for (const id of Array.from(this._handlers.keys())) this.disconnect(id);
        this._proxy = null;
    }
}
