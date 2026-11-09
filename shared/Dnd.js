// @shared Dnd.js
//
// Cinnamon's system-wide Do Not Disturb switch: the gsettings key
// org.cinnamon.desktop.notifications display-notifications (false = DND).
// That key is what System Settings → Notifications, inhibit@cinnamon.org
// and the message tray itself read, so an applet that wants to agree with
// them reads and writes it here. Pure Gio: runs under plain `cjs`.
//
//   const Dnd = require('./modules/shared/Dnd');
//   const dnd = new Dnd.Dnd();
//   dnd.active            → true while notifications are turned off system-wide
//   dnd.set(true)         → turn Do Not Disturb on (writes the key); returns true if it changed
//   dnd.toggle()
//   dnd.onChanged(active => …)   → handler id; fires on every change from anywhere
//   dnd.disconnect(id); dnd.destroy()
//
// Note for applets that keep their own DND state (Hush): while this key is
// false the tray destroys every queued banner, critical ones included
// (messageTray.js _updateState), so nothing you hand it will be shown.

const Gio = imports.gi.Gio;

var SCHEMA = 'org.cinnamon.desktop.notifications';
var KEY = 'display-notifications';

class Dnd {
    constructor(settings) {
        this._settings = settings || new Gio.Settings({ schema_id: SCHEMA });
        this._handlers = new Map();   // id → gsettings signal id
        this._nextId = 1;
        this._destroyed = false;
    }

    get active() {
        if (this._destroyed) return false;
        return !this._settings.get_boolean(KEY);
    }

    set(active) {
        if (this._destroyed) return false;
        const want = !active;
        if (this._settings.get_boolean(KEY) === want) return false;
        this._settings.set_boolean(KEY, want);
        return true;
    }

    toggle() {
        return this.set(!this.active);
    }

    onChanged(cb) {
        if (this._destroyed) return 0;
        const id = this._nextId++;
        const sid = this._settings.connect('changed::' + KEY, () => cb(this.active));
        this._handlers.set(id, sid);
        return id;
    }

    disconnect(id) {
        const sid = this._handlers.get(id);
        if (sid === undefined) return;
        this._handlers.delete(id);
        this._settings.disconnect(sid);
    }

    destroy() {
        if (this._destroyed) return;
        for (const id of Array.from(this._handlers.keys())) this.disconnect(id);
        this._destroyed = true;
        this._settings = null;
    }
}
