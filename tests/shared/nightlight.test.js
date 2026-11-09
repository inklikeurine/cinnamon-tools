// cinnamon-tools/shared/NightLight.js: time formatting, schedule end, and the
// gsettings side on a private in-memory backend (never the desktop's keys).
// Run via: python3 ../cinnamon-tools/harness/run.py shared --tests --suite nightlight
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const require = T.makeRequire(ARGV[0]);
const NL = require('./modules/shared/NightLight');

T.run('formatHours', () => {
    T.eq(NL.formatHours(6), '06:00', 'whole hour');
    T.eq(NL.formatHours(6.5), '06:30', 'half hour');
    T.eq(NL.formatHours(20.25), '20:15', 'evening');
    T.eq(NL.formatHours(23.9999), '00:00', 'rounds over midnight');
    T.eq(NL.formatHours(NaN), '', 'NaN');
    T.eq(NL.formatHours(24), '', 'out of range');
    T.eq(NL.formatHours(undefined), '', 'missing');
});

T.run('untilHours by schedule mode', () => {
    T.eq(NL.untilHours('manual', 7, 6.2), 7, 'manual uses the "to" hour');
    T.eq(NL.untilHours('auto', 7, 6.2), 6.2, 'auto uses sunrise');
    T.ok(isNaN(NL.untilHours('always', 7, 6.2)), 'always never ends');
});

T.runAsync('gsettings side on a memory backend (skipped without the schema)', async () => {
    const source = Gio.SettingsSchemaSource.get_default();
    if (!source || !source.lookup(NL.SCHEMA, true)) { T.skip('night light gsettings', 'schema ' + NL.SCHEMA + ' not installed'); return; }
    const settings = Gio.Settings.new_with_backend(NL.SCHEMA, Gio.memory_settings_backend_new());
    const nl = await NL.NightLight.create({ settings, noDaemon: true });
    T.ok(nl.available, 'available with the schema');
    let fired = 0;
    nl.onChanged(() => fired++);
    nl.setEnabled(false);
    T.eq(nl.setEnabled(true), true, 'switched on');
    T.eq(nl.enabled, true, 'enabled');
    T.eq(nl.active, true, 'active falls back to enabled without csd-color');
    T.eq(nl.pausedUntilTomorrow, false, 'never paused without csd-color');
    nl.setTemperature(99999);
    T.eq(nl.temperature, NL.MAX_K, 'clamped high');
    nl.setTemperature(10);
    T.eq(nl.temperature, NL.MIN_K, 'clamped low');
    let rejected = false;
    await nl.setPausedUntilTomorrow(true).catch(() => { rejected = true; });
    T.ok(rejected, 'pause needs the daemon');
    await new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => { r(); return false; }));
    T.ok(fired >= 2, 'changes were reported');
    nl.destroy();
    T.eq(nl.available, false, 'unavailable after destroy');
});

T.runAsync('no schema → unavailable, never throws', async () => {
    const nl = new NL.NightLight(null, null);
    T.eq(nl.available, false, 'unavailable');
    T.eq(nl.enabled, false, 'off');
    T.eq(nl.setEnabled(true), false, 'cannot switch');
    T.eq(nl.untilText(), '', 'no end time');
    nl.destroy();
});

T.exit();
