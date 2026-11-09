# Cinnamon Spices submission rules

Source: `validate-spice` and `README.md` in
<https://github.com/linuxmint/cinnamon-spices-applets>, checked 2026-09-13.
An applet repo's root is the Spices layout on purpose, so its `<uuid>/`
folder (built by `package.py --spices`) drops into a fork of that repository
unchanged. `check.py` reports
these as `spices:` warnings; `package.py` promotes them to errors.

## What validate-spice enforces (verbatim conditions)

Required, else `Missing file`:

```
info.json
screenshot.png
files/<uuid>/metadata.json
files/<uuid>/icon.png
```

Forbidden: `icon.png` at `<uuid>/` level (`Forbidden file`).

`files/` must contain exactly one entry:

```python
if len(os.listdir("files")) != 1:
    raise CheckError("[%s] The files directory should ONLY contain the $uuid directory!" % (uuid))
```

`metadata.json`:

```python
for field in ['icon', 'dangerous', 'last-edited']:
    if field in metadata: raise CheckError("[%s] Forbidden field '%s' in %s" ...)
for field in ['uuid', 'name', 'description']:
    if field not in metadata: raise CheckError("[%s] Missing field '%s' in %s" ...)
if metadata['uuid'] != uuid: raise CheckError("[%s] Wrong uuid in %s" ...)
```

plus "No Unicode characters permitted in any field" (ASCII only).

`info.json`: `author` required, no whitespace in it.

`icon.png`: opened with PIL; width must equal height.

Translations: `.po`/`.pot` only under `files/<uuid>/po/`; at most one `.pot`;
no compiled `.mo`; `msgfmt`/`xgettext` must accept them.

UUID: no whitespace; must match the directory name.

## What it does not police

Extra entries at `<uuid>/` level. `weather@mockturtl` upstream ships
`CHANGELOG.md`, `README.md`, `src/`, `test.sh`, `webpack.config.js` there.
These applets are stricter: `package.py --spices` builds the `<uuid>/`
folder from exactly five entries (`info.json`, `README.md`, `CHANGELOG.md`,
`screenshot.png`, `files/`), so an applet's `tests/` and repository files
never reach Spices.

## Other upstream rules (README)

- No pre-compiled blobs besides icons and images; no closed-source code; no
  code for binary-compiled languages.
- The spice must not fetch and execute code from external sources at runtime.
- External dependencies only as system packages from the distro repositories.
- The repository is GPL-3.0. Public-domain (Unlicense) code is GPL-compatible,
  so submission is unaffected; do not add a per-file license header.

## Version field

Spices does not compare `version` for updates; it uses `last-edited`, which
the Spices build injects into `metadata.json` (hence the field is forbidden
in submissions). `version` is informational for users and for this repo's
own rules (semver, CHANGELOG entry).
