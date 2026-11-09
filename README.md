# cinnamon-tools

Shared tools for the Cinnamon panel applets at
[inklikeurine](https://github.com/inklikeurine): the checks every applet
must pass, the test runner, the modules several applets share, and the
templates new applets start from.

The applets are AI-generated, and the maintainer doesn't read code. These
tools are how a change gets checked anyway: the same commands run on every
pull request, and an applet's `AGENTS.md` tells contributors (and their AI
tools) how to run them first.

## What's here

| Path | What it is |
|---|---|
| `tools/check.py` | **Validator.** Checks an applet's files, `info.json`, `metadata.json`, `settings-schema.json`, README sections, that every `.js` file parses under `cjs` and every `.py` file compiles, the calls that freeze Cinnamon, the version, and the shared-module copies |
| `harness/run.py` | **Test runner.** Runs an applet's tests under `cjs` and `python3` without a Cinnamon session, and a live check that loads it into the running Cinnamon desktop |
| `harness/lib.js` | The small assertion library the `cjs` tests use |
| `tools/pr_guards.py` | The pull request guards (see below). Reads the change only; never runs it |
| `tools/fail_first.py` | Runs a pull request's new tests against `main`'s code, to prove they test the change |
| `tools/new_applet.py` | Creates a new applet repo from the templates |
| `tools/sync_shared.py` | Copies the shared modules into the applets that use them |
| `tools/package.py` | Builds a release zip, or (`--spices`) the folder to send to Cinnamon Spices |
| `tools/on_edit.py` | Runs the validator after each edit an AI tool makes (a Claude Code hook) |
| `tools/ci_setup.sh` | Installs what the checks need inside the Linux Mint container on GitHub |
| `shared/` | Modules several applets use, kept once here and copied into each (see `shared/README.md`) |
| `templates/` | The starting files for a new applet, including its `AGENTS.md` and pull request checks (`templates/repo/`) |
| `docs/` | How Cinnamon's JavaScript engine behaves (`cinnamon-reference.md`) and what Cinnamon Spices accepts (`spices.md`) |
| `tests/shared/` | Tests for the shared modules and for these tools |

## Setup

Needs Linux Mint 22.3 or newer with Cinnamon 6.6 or newer, which brings
everything the tools use (`cjs`, Python 3 with its GObject libraries). The
tools use nothing outside Python's standard library. Clone this repository
next to the applet repositories, so each applet can reach it as
`../cinnamon-tools`:

```sh
cd ~/src/cinnamon
git clone https://github.com/inklikeurine/cinnamon-tools
```

How to run the checks for one applet is in that applet's `AGENTS.md`.

## Layout

```text
~/src/cinnamon/
├── cinnamon-tools/          # this repository
└── <name>/                  # one repository per applet, e.g. hush/
    ├── info.json  README.md  CHANGELOG.md  screenshot.png
    ├── files/<uuid>/        # the applet itself, as installed
    └── tests/
        ├── *.test.js        # test suites, run under cjs
        ├── *.test.py        # test suites, run under python3
        ├── fixtures/        # sample data (made-up only)
        └── harness.json     # live-check settings and the shared modules the applet uses
```

The tools find an applet by its folder name: `check.py hush` checks
`../hush/` (or `../cinnamon-hush/`, as a plain `git clone` names it).
Harness output goes to `out/` here, which git ignores.

## Pull request checks

Each applet has two workflows, copied from `templates/repo/`:

- **`checks.yml`: Validate and test.** Runs the validator and the tests on
  every push to `main` and every pull request, in a Linux Mint 22.3
  container so the tests get Cinnamon 6.6's own engine and libraries. On a
  pull request it also runs `tools/fail_first.py`: the pull request's new
  or changed test suites must fail against the code on `main`, or they
  don't test the change.
- **`guards.yml`: Guards.** Runs `tools/pr_guards.py`, which only reads the
  change. GitHub runs this workflow as it is on `main`, so a pull request
  can't edit it away. It fails when a pull request edits or removes an
  existing test, gives the applet new reach, or changes `.github/`,
  `.claude/`, `AGENTS.md` or `CLAUDE.md`.

New reach, for an applet, means its shipped code (`files/<uuid>/`) now:
runs a program it didn't before; uses a system service or settings store it
didn't before (a name like `org.bluez.Device1`); contains a new web
address; uses the network for the first time; or adds or changes `role` or
`external-configuration-app` in `metadata.json`.

The maintainer can allow each of those with a label on the pull request:

| Label | Allows |
|---|---|
| `tests-changed` | editing or removing existing tests |
| `new-access` | new programs, system services, web addresses or network use |
| `rules-changed` | changes to the checks or the AI tools' files |
| `no-test-needed` | a change to shipped code with no test that fails without it |

**The live check doesn't run on GitHub**: it needs a running Cinnamon
desktop. A pull request's **Automated Test** tick means the checks passed
*and* someone ran the live check on a Cinnamon desktop.

A pull request in an applet whose branch has the same name as a branch here
is checked with that branch of the tools, so a change that needs both can be
tested together. Otherwise the tools' `main` is used.

## Checks on this repository

A change here changes every applet's checks, so this repository's own
`checks.yml` runs each public applet's validator and tests (against that
applet's `main`, or a branch with the same name as this pull request's)
with the tools from the pull request, plus the tests in `tests/shared/`.
Dependabot's updates go through it too: merge one only when it's green.

## The live check

What `harness/run.py <uuid> --live` (or `--spec`) does to the running
Cinnamon desktop, and all it does, is listed in every applet's `AGENTS.md`
under "The live check". In short: it links the applet's folder into
`~/.local/share/cinnamon/applets/`, adds it to a panel if it isn't on one,
reloads it, runs the checks in `tests/harness.json` inside Cinnamon, reads
the error log, and puts the panel and the applet's settings back the way
they were. It never restarts Cinnamon.

It proves the applet loads, runs without errors, and does what the checks
assert. It does **not** prove the applet looks right; a person checks that.

## License

[Unlicense](UNLICENSE): public domain.
