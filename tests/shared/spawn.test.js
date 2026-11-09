// cinnamon-tools/shared/Spawn.js under plain cjs. Run via:
//   python3 ../cinnamon-tools/harness/run.py shared --tests --suite spawn
// ARGV[0] = staged fake applet dir (modules/shared/Spawn.js inside), ARGV[1] = harness dir.
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;
const require = T.makeRequire(ARGV[0]);
const Spawn = require('./modules/shared/Spawn');

T.run('exports', () => {
    T.eq(typeof Spawn.run, 'function', 'run');
    T.eq(typeof Spawn.available, 'function', 'available');
    T.eq(typeof Spawn.LineReader, 'function', 'LineReader');
});

T.run('available()', () => {
    T.eq(Spawn.available('sh'), true, 'sh exists');
    T.eq(Spawn.available('definitely-not-a-program-xyz'), false, 'missing program');
});

T.runAsync('run(): stdout, status 0', done => {
    Spawn.run(['sh', '-c', 'echo hello; echo err 1>&2']).then(r => {
        T.eq(r.ok, true, 'ok');
        T.eq(r.status, 0, 'status');
        T.eq(r.stdout, 'hello\n', 'stdout');
        T.eq(r.stderr, 'err\n', 'stderr');
        T.eq(r.error, null, 'no error');
        T.eq(r.timedOut, false, 'not timed out');
        done();
    });
});

T.runAsync('run(): non-zero exit is not an error', done => {
    Spawn.run(['sh', '-c', 'exit 3']).then(r => {
        T.eq(r.ok, false, 'not ok');
        T.eq(r.status, 3, 'status 3');
        T.eq(r.error, null, 'no spawn error');
        done();
    });
});

T.runAsync('run(): missing binary resolves, never throws', done => {
    Spawn.run(['/nonexistent/binary-xyz']).then(r => {
        T.eq(r.ok, false, 'not ok');
        T.eq(r.status, -1, 'status -1');
        T.ok(typeof r.error === 'string' && r.error.length > 0, 'error message');
        done();
    });
});

T.runAsync('run(): stdin input', done => {
    Spawn.run(['cat'], { input: 'piped\n' }).then(r => {
        T.eq(r.stdout, 'piped\n', 'echoed');
        done();
    });
});

T.runAsync('run(): timeout kills the process', done => {
    const t0 = GLib.get_monotonic_time();
    Spawn.run(['sleep', '10'], { timeoutMs: 300 }).then(r => {
        T.eq(r.ok, false, 'not ok');
        T.eq(r.timedOut, true, 'timedOut');
        T.ok((GLib.get_monotonic_time() - t0) / 1000 < 5000, 'returned promptly');
        done();
    });
}, 10);

T.runAsync('run(): cancellable', done => {
    const c = new Gio.Cancellable();
    Spawn.run(['sleep', '10'], { cancellable: c }).then(r => {
        T.eq(r.ok, false, 'not ok');
        T.ok(r.error !== null || r.status !== 0, 'cancelled reports failure');
        done();
    });
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 200, () => { c.cancel(); return GLib.SOURCE_REMOVE; });
}, 10);

T.runAsync('LineReader: lines then exit', done => {
    const lines = [];
    const reader = new Spawn.LineReader(['sh', '-c', 'echo a; echo b; sleep 0.2; echo c'], {
        onLine: l => lines.push(l),
        onExit: ({ status, error }) => {
            T.eq(lines, ['a', 'b', 'c'], 'all lines, newline stripped');
            T.eq(status, 0, 'exit status');
            T.eq(error, null, 'no error');
            T.eq(reader.running, false, 'not running after exit');
            done();
        }
    });
    T.eq(reader.start(), true, 'started');
    T.eq(reader.running, true, 'running');
});

T.runAsync('LineReader: stop() ends callbacks and kills the process', done => {
    let count = 0, exited = false;
    const reader = new Spawn.LineReader(['sh', '-c', 'while true; do echo tick; sleep 0.05; done'], {
        onLine: () => { count++; },
        onExit: () => { exited = true; }
    });
    reader.start();
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
        const before = count;
        reader.stop();
        T.eq(reader.running, false, 'stopped');
        T.ok(before > 0, 'received lines before stop');
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
            T.eq(count, before, 'no lines after stop');
            T.eq(exited, false, 'onExit not called after stop()');
            done();
            return GLib.SOURCE_REMOVE;
        });
        return GLib.SOURCE_REMOVE;
    });
}, 10);

T.runAsync('LineReader: missing binary → onExit with error, start() false', done => {
    const reader = new Spawn.LineReader(['/nonexistent/binary-xyz'], {
        onExit: ({ status, error }) => {
            T.eq(status, -1, 'status -1');
            T.ok(typeof error === 'string', 'error message');
            T.eq(reader.running, false, 'not running');
            done();
        }
    });
    T.eq(reader.start(), false, 'start() returned false');
});

T.exit();
