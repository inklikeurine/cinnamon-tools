// cinnamon-tools/shared/Dnd.js under plain cjs against the real gsettings key
// (read, and a write of the value it already has — nothing changes).
// Run via: python3 ../cinnamon-tools/harness/run.py shared --tests --suite dnd
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const Gio = imports.gi.Gio;
const require = T.makeRequire(ARGV[0]);
const DndMod = require('./modules/shared/Dnd');
const Dnd = DndMod.Dnd;

T.run('reads the live key and agrees with gsettings', () => {
    const d = new Dnd();
    const raw = new Gio.Settings({ schema_id: DndMod.SCHEMA }).get_boolean(DndMod.KEY);
    T.eq(d.active, !raw, 'active is the inverse of display-notifications');
    T.eq(d.set(d.active), false, 'setting the current value is a no-op');
    d.destroy();
    T.eq(d.active, false, 'inactive after destroy');
    T.eq(d.set(true), false, 'set after destroy does nothing');
    T.eq(d.onChanged(() => {}), 0, 'no handler after destroy');
});

T.run('onChanged / disconnect bookkeeping', () => {
    const d = new Dnd();
    const a = d.onChanged(() => {}), b = d.onChanged(() => {});
    T.ok(a > 0 && b > a, 'ids');
    d.disconnect(a);
    d.disconnect(a);            // twice is fine
    d.disconnect(999);          // unknown is fine
    d.destroy();
    d.destroy();
});

T.runAsync('a change from outside fires onChanged with the new state (memory backend, no desktop change)', async () => {
    // A private in-memory settings object so the test never flips the desktop's key.
    let settings;
    try { settings = Gio.Settings.new_with_backend(DndMod.SCHEMA, Gio.memory_settings_backend_new()); }
    catch (e) { T.skip('memory backend', e.message); return; }
    const d = new Dnd(settings);
    const seen = [];
    d.onChanged(v => seen.push(v));
    T.eq(d.set(true), true, 'turned on');
    T.eq(d.active, true, 'active');
    T.eq(d.toggle(), true, 'toggled off');
    T.eq(d.active, false, 'inactive');
    await new Promise(r => imports.gi.GLib.timeout_add(imports.gi.GLib.PRIORITY_DEFAULT, 100, () => { r(); return false; }));
    T.eq(seen, [true, false], 'handler saw both changes');
    d.destroy();
});

T.exit();
