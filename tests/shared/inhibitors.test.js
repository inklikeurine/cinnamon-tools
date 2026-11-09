// cinnamon-tools/shared/Inhibitors.js under plain cjs against the real
// org.gnome.SessionManager: lists inhibitors, then takes one itself for well
// under a second (idle flag, app id "hush-shared-test") and releases it.
// Run via: python3 ../cinnamon-tools/harness/run.py shared --tests --suite inhibitors
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const require = T.makeRequire(ARGV[0]);
const Mod = require('./modules/shared/Inhibitors');
const Inhibitors = Mod.Inhibitors;

const bus = Gio.DBus.session;
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
function smCall(method, variant) {
    return new Promise((resolve, reject) => {
        bus.call(Mod.NAME, Mod.PATH, Mod.IFACE, method, variant || null, null, Gio.DBusCallFlags.NONE, 3000, null, (c, res) => {
            try { resolve(c.call_finish(res).deep_unpack()); } catch (e) { reject(e); }
        });
    });
}

T.run('exports', () => {
    T.eq(typeof Inhibitors.create, 'function', 'create');
    T.eq(Mod.Flags.IDLE, 8, 'flags');
});

T.runAsync('create() lists the current inhibitors; an inhibit taken by the test shows up and goes away', async () => {
    const inh = await Inhibitors.create();
    if (!inh.available) { T.skip('session manager', 'org.gnome.SessionManager has no owner'); inh.destroy(); return; }
    T.ok(Array.isArray(inh.list), 'list');
    const before = inh.list.length;
    T.eq(inh.find(['hush-shared-test']), null, 'ours not there yet');
    const changes = [];
    const id = inh.onChanged(list => changes.push(list.length));

    const [cookie] = await smCall('Inhibit', new GLib.Variant('(susu)', ['hush-shared-test', 0, 'Inhibitors.js test', Mod.Flags.IDLE]));
    T.ok(cookie > 0, 'inhibit cookie');
    let found = null;
    for (let i = 0; i < 30 && !found; i++) { await sleep(50); found = inh.find(['HUSH-SHARED-TEST']); }
    T.ok(found !== null, 'our inhibitor appeared (case-insensitive match)');
    if (found) {
        T.eq(found.reason, 'Inhibitors.js test', 'reason');
        T.eq(found.flags & Mod.Flags.IDLE, Mod.Flags.IDLE, 'idle flag');
        T.ok(found.path.startsWith('/org/gnome/SessionManager/Inhibitor'), 'path');
    }
    T.eq(inh.list.length, before + 1, 'one more');

    await smCall('Uninhibit', new GLib.Variant('(u)', [cookie]));
    let gone = false;
    for (let i = 0; i < 30 && !gone; i++) { await sleep(50); gone = inh.find(['hush-shared-test']) === null; }
    T.eq(gone, true, 'released');
    T.eq(inh.list.length, before, 'back to before');
    T.ok(changes.length >= 2, 'onChanged fired for add and remove');
    inh.disconnect(id);
    inh.destroy();
    T.eq(inh.available, false, 'unavailable after destroy');
    T.eq(inh.list, [], 'empty after destroy');
}, 15);

T.exit();
