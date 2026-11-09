// cinnamon-tools/shared/PowerProfiles.js: the pure property mappers, and (when the
// daemon runs) a read-only look at the live service.
// Run via: python3 ../cinnamon-tools/harness/run.py shared --tests --suite powerprofiles
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const require = T.makeRequire(ARGV[0]);
const PP = require('./modules/shared/PowerProfiles');

T.run('profileNames keeps known profiles in power-saver, balanced, performance order', () => {
    T.eq(PP.profileNames([{ Profile: 'balanced', Driver: 'placeholder' }, { Profile: 'power-saver' }]), ['power-saver', 'balanced'], 'desktop with placeholder driver');
    T.eq(PP.profileNames([{ Profile: 'performance' }, { Profile: 'power-saver' }, { Profile: 'balanced' }]), ['power-saver', 'balanced', 'performance'], 'laptop');
    T.eq(PP.profileNames([{ Profile: 'turbo' }, null, {}]), [], 'unknown and malformed entries dropped');
    T.eq(PP.profileNames(undefined), [], 'missing property');
});

T.run('holdList maps ActiveProfileHolds', () => {
    T.eq(PP.holdList([{ ApplicationId: 'org.gnome.Videos', Profile: 'performance', Reason: 'Playing' }]),
        [{ appId: 'org.gnome.Videos', profile: 'performance', reason: 'Playing' }], 'one hold');
    T.eq(PP.holdList(null), [], 'none');
});

T.runAsync('live daemon, read only (skipped when absent)', async () => {
    let pp;
    try { pp = await PP.PowerProfiles.create(); } catch (e) { T.skip('live power-profiles-daemon', 'system bus unreachable: ' + e.message); return; }
    if (!pp.available) { pp.destroy(); T.skip('live power-profiles-daemon', 'not running'); return; }
    T.ok(pp.profiles.length >= 1, 'at least one profile');
    T.ok(pp.profiles.includes(pp.active), 'active profile is one of the offered ones');
    T.ok(Array.isArray(pp.holds), 'holds is a list');
    let rejected = false;
    await pp.set('no-such-profile').catch(() => { rejected = true; });
    T.ok(rejected, 'unknown profile rejected without touching the daemon');
    pp.destroy();
    T.eq(pp.available, false, 'unavailable after destroy');
    T.eq(pp.profiles, [], 'no profiles after destroy');
});

T.exit();
