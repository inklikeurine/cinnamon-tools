// cinnamon-tools/shared/DBusProxy.js under plain cjs against the real session/system
// buses (read-only). Run via: python3 ../cinnamon-tools/harness/run.py shared --tests --suite dbusproxy
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;
const require = T.makeRequire(ARGV[0]);
const DBusProxy = require('./modules/shared/DBusProxy').DBusProxy;

T.run('exports a class with create()', () => {
    T.eq(typeof DBusProxy, 'function', 'class');
    T.eq(typeof DBusProxy.create, 'function', 'create');
});

T.runAsync('session bus: org.freedesktop.DBus.ListNames', done => {
    DBusProxy.create({ bus: 'session', name: 'org.freedesktop.DBus', path: '/org/freedesktop/DBus', iface: 'org.freedesktop.DBus' })
        .then(p => {
            T.eq(p.available, true, 'the bus daemon owns its name');
            return p.call('ListNames').then(([names]) => {
                T.ok(Array.isArray(names) && names.includes('org.freedesktop.DBus'), 'ListNames unpacked');
                p.destroy();
                T.eq(p.available, false, 'available false after destroy');
                return p.call('ListNames').then(() => T.ok(false, 'call after destroy must reject'), e => T.ok(/destroyed/.test(e.message), 'rejects after destroy'));
            });
        })
        .catch(e => T.ok(false, 'unexpected: ' + e.message))
        .then(done);
});

T.runAsync('well-known name with no owner: available false, get() undefined', done => {
    DBusProxy.create({ bus: 'session', name: 'org.example.NoSuchService', path: '/org/example', iface: 'org.example.Iface',
                       flags: Gio.DBusProxyFlags.DO_NOT_AUTO_START })
        .then(p => {
            T.eq(p.available, false, 'no owner');
            T.eq(p.get('Anything'), undefined, 'no cached property');
            T.eq(JSON.stringify(p.properties()), '{}', 'properties() empty');
            const id = p.onOwnerChanged(() => {});
            T.ok(id > 0, 'handler id');
            p.disconnect(id);
            p.destroy();
            p.destroy();   // idempotent
            T.eq(p.get('Anything'), undefined, 'get after destroy');
        })
        .catch(e => T.ok(false, 'unexpected: ' + e.message))
        .then(done);
});

T.runAsync('system bus: cached properties of power-profiles-daemon (skips if absent)', done => {
    DBusProxy.create({ bus: 'system', name: 'org.freedesktop.UPower.PowerProfiles', path: '/org/freedesktop/UPower/PowerProfiles',
                       iface: 'org.freedesktop.UPower.PowerProfiles', flags: Gio.DBusProxyFlags.DO_NOT_AUTO_START })
        .then(p => {
            if (!p.available) { print('    (power-profiles-daemon not on the bus; skipped)'); p.destroy(); return; }
            const active = p.get('ActiveProfile');
            const profiles = p.get('Profiles');
            T.eq(typeof active, 'string', 'ActiveProfile is a string');
            T.ok(Array.isArray(profiles) && profiles.length >= 1, 'Profiles is a non-empty array');
            T.ok(profiles.some(pr => pr.Profile === active), 'active profile is listed (deepUnpack of a{sv})');
            const props = p.properties();
            T.eq(props.ActiveProfile, active, 'properties() agrees with get()');
            const id = p.onPropertiesChanged(() => {});
            const sid = p.onSignal('ProfileReleased', () => {});
            T.ok(id > 0 && sid > id, 'two handler ids');
            p.destroy();
        })
        .catch(e => T.ok(false, 'unexpected: ' + e.message))
        .then(done);
});

T.runAsync('create() rejects malformed arguments instead of hanging', done => {
    // GDBus asserts and never calls back on a bad path/name; the wrapper must reject up front.
    Promise.all([
        DBusProxy.create({ bus: 'session', name: 'org.freedesktop.DBus', path: 'not-a-path', iface: 'org.freedesktop.DBus' })
            .then(() => T.ok(false, 'bad path should reject'), e => T.ok(/object path/.test(e.message), 'bad path rejected')),
        DBusProxy.create({ bus: 'session', name: 'not a name', path: '/x', iface: 'org.freedesktop.DBus' })
            .then(() => T.ok(false, 'bad name should reject'), e => T.ok(/bus name/.test(e.message), 'bad name rejected')),
        DBusProxy.create({ bus: 'session', name: 'org.freedesktop.DBus', path: '/x', iface: 'noiface' })
            .then(() => T.ok(false, 'bad iface should reject'), e => T.ok(/interface/.test(e.message), 'bad iface rejected')),
    ]).then(() => done());
});

T.exit();
