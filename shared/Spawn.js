// @shared Spawn.js
//
// Async subprocess helpers for Cinnamon applets. Pure GLib/Gio: runs under
// plain `cjs` as well as inside the shell. Nothing here blocks the main
// loop, and nothing here ever throws or rejects for an ordinary failure —
// a missing binary, a non-zero exit, a timeout — because an applet must
// degrade quietly (AGENTS.md → "Fail quietly, degrade gracefully").
//
//   const Spawn = require('./modules/shared/Spawn');
//
//   Spawn.available('pactl')                       → true/false (GLib.find_program_in_path)
//
//   Spawn.run(['pactl', '-f', 'json', 'list', 'sinks'], { timeoutMs: 5000 })
//     → Promise<{ ok, status, stdout, stderr, timedOut, error }>
//        ok       true iff the process exited with status 0
//        status   exit status, or -1 when it never ran / was killed
//        error    the GLib.Error message when it could not be spawned, else null
//     Options: timeoutMs (force_exit after that; default none), input (string
//     written to stdin), cancellable (Gio.Cancellable; cancelling kills it).
//
//   const reader = new Spawn.LineReader(['piactl', 'monitor', 'connectionstate'], {
//       onLine: line => ...,            // one call per stdout line, newline stripped
//       onExit: ({ status, error }) => ... // once, after the last line
//   });
//   reader.start();  reader.running;  reader.stop();   // stop() = force_exit + no more callbacks
//
// Teardown: every LineReader an applet starts must be stop()ped in
// on_applet_removed_from_panel(); run() calls that are still in flight are
// harmless (their promise resolves into nothing) but pass a cancellable if
// the callback would touch destroyed actors.

const GLib = imports.gi.GLib;
const Gio  = imports.gi.Gio;

function available(program) {
    return GLib.find_program_in_path(program) !== null;
}

function run(argv, options) {
    const opts = options || {};
    return new Promise(resolve => {
        const result = { ok: false, status: -1, stdout: '', stderr: '', timedOut: false, error: null };
        let flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE;
        if (typeof opts.input === 'string') flags |= Gio.SubprocessFlags.STDIN_PIPE;
        let proc;
        try {
            proc = new Gio.Subprocess({ argv, flags });
            proc.init(null);
        } catch (e) {
            result.error = e.message;
            resolve(result);
            return;
        }
        let timerId = 0;
        if (opts.timeoutMs > 0) {
            timerId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, opts.timeoutMs, () => {
                timerId = 0;
                result.timedOut = true;
                proc.force_exit();
                return GLib.SOURCE_REMOVE;
            });
        }
        proc.communicate_utf8_async(typeof opts.input === 'string' ? opts.input : null, opts.cancellable || null, (p, res) => {
            if (timerId) { GLib.source_remove(timerId); timerId = 0; }
            try {
                const [, stdout, stderr] = p.communicate_utf8_finish(res);
                result.stdout = stdout || '';
                result.stderr = stderr || '';
            } catch (e) {
                result.error = e.message;
            }
            // A cancelled communicate() may not have reaped the child yet, and
            // GLib asserts if get_if_exited() is asked before that.
            if (!result.error && p.get_if_exited()) result.status = p.get_exit_status();
            result.ok = result.status === 0 && !result.timedOut && !result.error;
            resolve(result);
        });
    });
}

class LineReader {
    constructor(argv, callbacks) {
        this._argv = argv;
        this._onLine = (callbacks && callbacks.onLine) || (() => {});
        this._onExit = (callbacks && callbacks.onExit) || (() => {});
        this._proc = null;
        this._stream = null;
        this._cancellable = null;
        this._stopped = false;
        this.running = false;
    }

    start() {
        if (this.running) return true;
        this._stopped = false;
        try {
            this._proc = new Gio.Subprocess({ argv: this._argv, flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE });
            this._proc.init(null);
        } catch (e) {
            this._proc = null;
            this._onExit({ status: -1, error: e.message });
            return false;
        }
        this.running = true;
        this._cancellable = new Gio.Cancellable();
        this._stream = new Gio.DataInputStream({ base_stream: this._proc.get_stdout_pipe() });
        this._proc.wait_async(null, (p, res) => {
            let status = -1;
            try { p.wait_finish(res); if (p.get_if_exited()) status = p.get_exit_status(); } catch (e) { /* killed */ }
            this._exited = { status, error: null };
            this._maybeFinish();
        });
        this._readNext();
        return true;
    }

    _readNext() {
        this._stream.read_line_async(GLib.PRIORITY_DEFAULT, this._cancellable, (s, res) => {
            let line = null;
            try {
                const [bytes] = s.read_line_finish(res);
                if (bytes !== null) line = new TextDecoder().decode(bytes);
            } catch (e) {
                line = null;   // cancelled or read error → treat as end of stream
            }
            if (this._stopped) return;
            if (line === null) {
                this._eof = true;
                this._maybeFinish();
                return;
            }
            this._onLine(line);
            if (!this._stopped) this._readNext();
        });
    }

    _maybeFinish() {
        if (this._stopped || !this._eof || !this._exited) return;
        const exited = this._exited;
        this._teardown();
        this._onExit(exited);
    }

    stop() {
        if (!this.running) return;
        this._stopped = true;
        this._teardown(true);
    }

    _teardown(kill) {
        this.running = false;
        if (this._cancellable) { this._cancellable.cancel(); this._cancellable = null; }
        if (this._stream) { try { this._stream.close(null); } catch (e) { /* already closed */ } this._stream = null; }
        if (this._proc) {
            if (kill) this._proc.force_exit();
            this._proc = null;
        }
        this._eof = false;
        this._exited = null;
    }
}
