// __UUID__ smoke test: the applet's files parse under cjs and the metadata
// says what AGENTS.md requires. Runs headless via run.py:
//   cjs <this file> <appletDir> <harnessDir> <testsDir>
//
// Add real suites next to this one (<topic>.test.js) as the applet grows.
// Load a module the way Cinnamon does —
//   const require = T.makeRequire(ARGV[0]);
//   const Foo = require('./modules/Foo');
// never with imports.searchPath + imports.modules.<Name> (the GJS importer,
// which Cinnamon cannot reload). Exercise it with T.run /
// T.runAsync (callback or Promise) / T.eq / T.ok; T.skip(name, reason) when a
// CLI or daemon is absent; sample data via T.fixture(name) from
// tests/fixtures/.
imports.searchPath.unshift(ARGV[1]);
const T = imports.lib;
const GLib = imports.gi.GLib;

const appletDir = ARGV[0];

T.run('metadata.json is consistent', () => {
    const meta = T.readJson(appletDir + '/metadata.json');
    T.eq(meta.uuid, '__UUID__', 'uuid');
    T.ok(/^\d+\.\d+\.\d+$/.test(meta.version), 'semver version');
    T.eq(meta['cinnamon-version'], ['6.6'], 'cinnamon-version');
});

T.run('applet.js parses under cjs and defines main()', () => {
    const src = T.readFile(appletDir + '/applet.js');
    T.ok(T.parses(src), 'parses');
    T.ok(/function\s+main\s*\(/.test(src), 'has main()');
});

T.run('settings-schema.json keys are bound in applet.js (if present)', () => {
    const schemaPath = appletDir + '/settings-schema.json';
    if (!GLib.file_test(schemaPath, GLib.FileTest.EXISTS)) return;
    const schema = T.readJson(schemaPath);
    const src = T.readFile(appletDir + '/applet.js');
    for (const key of Object.keys(schema)) {
        const t = schema[key].type;
        if (['layout', 'page', 'section', 'header', 'button', 'label'].includes(t)) continue;
        T.ok(src.includes("'" + key + "'") || src.includes('"' + key + '"'), 'key bound: ' + key);
    }
});

T.exit();
