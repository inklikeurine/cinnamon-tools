// Minimal assertion library for running applet modules under plain `cjs`
// (no Cinnamon session). run.py invokes every suite as
//
//   cjs <suite>.test.js <appletDir> <harnessDir> <testsDir>
//
// and a suite starts with:
//
//   imports.searchPath.unshift(ARGV[1]);   // this dir → imports.lib
//   const T = imports.lib;
//   const require = T.makeRequire(ARGV[0]); // applet dir: files/<uuid>/
//
// T.run(name, fn)            synchronous test; exceptions are failures
// T.runAsync(name, fn, sec)  fn(done): call done() when finished — or, when fn takes
//                            no argument, fn() returns a Promise and the test ends when
//                            it settles (a rejection is a failure). Blocks a main loop;
//                            times out after sec (default 20)
// T.skip(name, reason)       record a skipped test (counted separately, never a failure);
//                            use for suites that need a CLI or daemon that is absent
// T.eq(a, b, msg) / T.ok(cond, msg)
// T.fixture(name)            text of <applet>/tests/fixtures/<name> (ARGV[2]/fixtures)
// T.fixtureJson(name)        the same, parsed
// T.tmpDir(prefix) / T.writeFile(path, text) / T.readFile(path) / T.readJson(path) / T.mkdirp(path)
// T.parses(src)              true if `src` parses as a function body (syntax check, no execution)
// T.makeRequire(appletDir)   a require() that loads files the way Cinnamon's loader does
// T.listDir(path)            file names in a directory, sorted
// T.summary()                prints PASSED/FAILED and returns the exit code
// T.exit()                   summary() + exit with that code
//
// When the environment variable HARNESS_RESULTS names a file, summary() also
// writes one {name, ok} entry per test there (ok null for a skipped test).
// fail_first.py uses it to say which new tests fail without a change.
//
// Cinnamon's own js/ tree (/usr/share/cinnamon/js) is appended to the search
// path so `require('misc.signalManager')` and friends resolve headless exactly
// as they do in the shell. Never load *applet* modules through imports.searchPath
// — that is the GJS importer, which Cinnamon cannot reload.
//
// Exit code: 0 = every assertion passed, 1 = a failure or a timeout.

const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;

const CINNAMON_JS = '/usr/share/cinnamon/js';
if (!imports.searchPath.includes(CINNAMON_JS) && GLib.file_test(CINNAMON_JS, GLib.FileTest.IS_DIR))
    imports.searchPath.push(CINNAMON_JS);

// Stub the globals that applet modules expect inside Cinnamon.
globalThis.global = globalThis.global || {
    log:        m => print('    log: ' + m),
    logWarning: m => print('    warn: ' + m),
    logError:   m => print('    error: ' + m)
};
if (typeof globalThis._ !== 'function') globalThis._ = s => s;

var failures = 0;
var passes   = 0;
var skipped  = 0;
var results  = [];   // one {name, ok} per T.run / T.runAsync / T.skip, in order

var eq = function(actual, expected, msg) {
    let a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a === e) { passes++; return; }
    failures++;
    print('    FAIL ' + (msg || '') + '\n      expected ' + e + '\n      actual   ' + a);
};

var ok = function(cond, msg) { eq(!!cond, true, msg); };

var run = function(name, fn) {
    print('  ' + name);
    let before = failures;
    try { fn(); } catch(e) { failures++; print('    THREW ' + e.message + '\n' + e.stack); }
    results.push({ name: name, ok: failures === before });
};

// Run an async test and block a main loop until it finishes. Two forms:
//   fn(done)  — callback style: call done() when finished
//   fn()      — promise style: the returned promise's settlement ends the test;
//               a rejection is reported as THREW and counted as a failure
var runAsync = function(name, fn, timeoutSec) {
    print('  ' + name);
    let before = failures;
    let loop = GLib.MainLoop.new(null, false);
    let finished = false;
    let done = () => { if (!finished) { finished = true; loop.quit(); } };
    let threw = e => { failures++; print('    THREW ' + (e && e.message !== undefined ? e.message : e) + (e && e.stack ? '\n' + e.stack : '')); done(); };
    GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, timeoutSec || 20, () => {
        if (!finished) { failures++; print('    TIMEOUT'); done(); }
        return GLib.SOURCE_REMOVE;
    });
    try {
        if (fn.length === 0) {
            let p = fn();
            if (p && typeof p.then === 'function') p.then(done, threw);
            else done();   // synchronous body; nothing to wait for
        } else {
            fn(done);
        }
    } catch(e) { threw(e); }
    if (!finished) loop.run();   // done() may already have fired synchronously
    results.push({ name: name, ok: failures === before });
};

var skip = function(name, reason) {
    skipped++;
    print('  ' + name + '\n    SKIPPED' + (reason ? ': ' + reason : ''));
    results.push({ name: name, ok: null });
};

var tmpDir = function(prefix) {
    return GLib.dir_make_tmp((prefix || 'xlet-test') + '-XXXXXX');
};

var writeFile = function(path, contents) {
    GLib.file_set_contents(path, contents);
};

var readFile = function(path) {
    let [ok, contents] = GLib.file_get_contents(path);
    return new TextDecoder().decode(contents);
};

var readJson = function(path) { return JSON.parse(readFile(path)); };

var mkdirp = function(path) {
    Gio.File.new_for_path(path).make_directory_with_parents(null);
};

var listDir = function(path) {
    let names = [], dir = GLib.Dir.open(path, 0), n;
    while ((n = dir.read_name()) !== null) names.push(n);
    dir.close();
    return names.sort();
};

// Fixtures live next to the suites in <applet>/tests/fixtures/ (committed;
// sanitised, never PII). run.py passes the tests dir as ARGV[2].
var fixturesDir = function() {
    if (typeof ARGV === 'undefined' || ARGV.length < 3 || !ARGV[2])
        throw new Error('T.fixture: no tests dir; run the suite through run.py or pass <applet>/tests/ as the third argument');
    return ARGV[2] + '/fixtures';
};

var fixture = function(name) { return readFile(fixturesDir() + '/' + name); };

var fixtureJson = function(name) { return JSON.parse(fixture(name)); };

// Mirror of Cinnamon's xlet loader (js/misc/fileUtils.js, createExports/requireModule):
// the source is evaluated in strict mode inside
//   Function(require, exports, module, __meta, __dirname, __filename)
// every top-level const/var/let/function/class becomes an export (core imports
// and GI namespaces excepted), files are cached per path, and paths are relative
// to the applet directory — from applet.js and from inside modules/ alike. Tests
// that load modules through this see exactly what the shell will see, including
// strict-mode errors and a missing trailing newline.
var makeRequire = function(appletDir) {
    const cache = new Map();
    const coreImports = ['mainloop', 'jsUnit', 'format', 'signals', 'lang', 'tweener', 'overrides',
                         'gettext', 'coverage', 'package', 'cairo', 'byteArray', 'cairoNative'];
    const giNames = new Set(['GLib', 'Gio', 'GObject', 'St', 'Clutter', 'Gdk', 'Gtk', 'Meta', 'Cinnamon',
                             'CinnamonDesktop', 'Pango', 'Soup', 'GdkPixbuf', 'Cogl', 'XApp', 'Cvc', 'NM']);
    function requireModule(path) {
        if (path.substr(0, 3) === 'gi.') return imports.gi[path.substr(3)];
        let prefix = path.split('.')[0];
        if (['ui', 'misc', 'perf'].includes(prefix) && path.substr(0, prefix.length + 1) === prefix + '.')
            return imports[prefix][path.substr(prefix.length + 1)];
        if (coreImports.includes(path)) return imports[path];
        if (path.substr(-3) !== '.js') path += '.js';
        if (path[0] === '.' || path[0] !== '/') path = appletDir + '/' + path.replace(/\.\//g, '');
        if (cache.has(path)) return cache.get(path);
        let JS = "'use strict';" + readFile(path) + ';';
        const exportsRe = /^module\.exports(\.[a-zA-Z0-9_$]+)?\s*=/m;
        const varRe = /^(?:'use strict';){0,}(const|var|let|function|class)\s+([a-zA-Z0-9_$]+)/gm;
        if (!exportsRe.test(JS)) {
            let m;
            while ((m = varRe.exec(JS)) !== null) {
                if (m.index === varRe.lastIndex) varRe.lastIndex++;
                const name = m[2];
                if (coreImports.includes(name.toLowerCase()) || giNames.has(name)) continue;
                JS += 'exports.' + name + ' = typeof ' + name + " !== 'undefined' ? " + name + ' : null;';
            }
        }
        JS += 'return module.exports;//# sourceURL=' + path;
        const exports = {};
        const module = { exports };
        const fn = new Function('require', 'exports', 'module', '__meta', '__dirname', '__filename', JS);
        const result = fn.call(exports, requireModule, exports, module, null, appletDir, path.split('/').pop());
        cache.set(path, result);
        return result;
    }
    return requireModule;
};

var parses = function(src) {
    try { new Function(src); return true; } catch(e) { print('    parse error: ' + e.message); return false; }
};

var summary = function() {
    print((failures ? 'FAILED' : 'PASSED') + ': ' + passes + ' assertions ok, ' + failures + ' failed' +
          (skipped ? ', ' + skipped + ' skipped' : ''));
    let out = GLib.getenv('HARNESS_RESULTS');
    if (out) {
        try { GLib.file_set_contents(out, JSON.stringify(results)); }
        catch(e) { print('    note: could not write HARNESS_RESULTS: ' + e.message); }
    }
    return failures === 0 ? 0 : 1;
};

var exit = function() {
    let code = summary();
    // Flush before leaving; imports.system.exit terminates immediately.
    imports.system.exit(code);
};
