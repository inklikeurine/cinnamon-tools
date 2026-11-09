// cinnamon-tools/harness/lib.js itself: the Promise form of runAsync, skip, fixtures
// and Cinnamon's js/ tree on the search path. Run via:
//   python3 ../cinnamon-tools/harness/run.py shared --tests --suite lib
// ARGV[0] = staged fake applet dir (modules/shared/Spawn.js inside),
// ARGV[1] = harness dir, ARGV[2] = cinnamon-tools/tests/shared/.
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const GLib = imports.gi.GLib;
const require = T.makeRequire(ARGV[0]);
const Spawn = require('./modules/shared/Spawn');

T.run('exports', () => {
    for (const f of ['run', 'runAsync', 'skip', 'eq', 'ok', 'fixture', 'fixtureJson', 'makeRequire', 'summary', 'exit'])
        T.eq(typeof T[f], 'function', f);
});

T.run('fixture(): reads <applet>/tests/fixtures/<name>', () => {
    T.eq(T.fixture('sample.json'), '{ "hello": "world", "n": 3 }\n', 'text');
    T.eq(T.fixtureJson('sample.json'), { hello: 'world', n: 3 }, 'json');
});

T.run('Cinnamon js/ is on the search path: imports.misc.* resolves headless', () => {
    T.ok(imports.searchPath.includes('/usr/share/cinnamon/js'), 'searchPath has /usr/share/cinnamon/js');
    T.eq(typeof imports.misc.signalManager.SignalManager, 'function', 'SignalManager class');
    // and through the applet-style require, the way a provider module loads it
    T.eq(require('misc.signalManager').SignalManager, imports.misc.signalManager.SignalManager, 'require(\'misc.signalManager\')');
});

T.runAsync('runAsync(): callback form still works', done => {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 10, () => { T.ok(true, 'callback fired'); done(); return GLib.SOURCE_REMOVE; });
});

T.runAsync('runAsync(): Promise form waits for the promise', () => {
    return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 10, () => { resolve(); return GLib.SOURCE_REMOVE; }))
        .then(() => Spawn.run(['sh', '-c', 'echo via-promise']))
        .then(r => { T.eq(r.stdout, 'via-promise\n', 'awaited a chained Spawn.run'); });
});

T.runAsync('runAsync(): async function form', async () => {
    const r = await Spawn.run(['sh', '-c', 'exit 0']);
    T.eq(r.ok, true, 'awaited inside async fn');
});

// The failure paths (rejection, sync throw, skip accounting) are exercised in a
// child cjs so this suite's own counters stay honest.
T.runAsync('runAsync() rejection → THREW + exit 1; skip() → "n skipped", exit 0', async () => {
    const dir = T.tmpDir('lib-test');
    const suite = dir + '/child.test.js';
    T.writeFile(suite, [
        "imports.searchPath.unshift(ARGV[1]);",
        "const T = imports.lib;",
        "T.skip('needs piactl', 'piactl not installed');",
        "T.runAsync('rejects', () => Promise.reject(new Error('boom')));",
        "T.runAsync('throws sync', () => { throw new Error('bang'); });",
        "T.runAsync('resolves', () => Promise.resolve());",
        "T.exit();", ""].join('\n'));
    const r = await Spawn.run(['cjs', suite, ARGV[0], ARGV[1], ARGV[2]], { timeoutMs: 30000 });
    T.eq(r.status, 1, 'child exits 1 (two failures)');
    T.ok(r.stdout.includes('SKIPPED: piactl not installed'), 'skip printed with reason');
    T.ok(r.stdout.includes('THREW boom'), 'rejection reported as THREW');
    T.ok(r.stdout.includes('THREW bang'), 'sync throw reported as THREW');
    T.ok(/FAILED: 0 assertions ok, 2 failed, 1 skipped/.test(r.stdout), 'summary counts failures and skips: ' + r.stdout.split('\n').pop());
    T.ok(!r.stdout.includes('TIMEOUT'), 'no timeout waiting on a rejected promise');

    const suite2 = dir + '/skip-only.test.js';
    T.writeFile(suite2, [
        "imports.searchPath.unshift(ARGV[1]);",
        "const T = imports.lib;",
        "T.skip('needs tailscale');",
        "T.run('trivial', () => T.ok(true, 'ok'));",
        "T.exit();", ""].join('\n'));
    const r2 = await Spawn.run(['cjs', suite2, ARGV[0], ARGV[1], ARGV[2]], { timeoutMs: 30000 });
    T.eq(r2.status, 0, 'skips alone never fail a suite');
    T.ok(/PASSED: 1 assertions ok, 0 failed, 1 skipped/.test(r2.stdout), 'summary shows the skip');

    const suite3 = dir + '/no-fixtures.test.js';
    T.writeFile(suite3, [
        "imports.searchPath.unshift(ARGV[1]);",
        "const T = imports.lib;",
        "T.run('fixture without tests dir', () => T.fixture('x'));",
        "T.exit();", ""].join('\n'));
    const r3 = await Spawn.run(['cjs', suite3, ARGV[0], ARGV[1]], { timeoutMs: 30000 });
    T.eq(r3.status, 1, 'fixture() without ARGV[2] is a clear failure');
    T.ok(r3.stdout.includes('no tests dir'), 'error names the missing argument');
}, 60);

T.exit();
