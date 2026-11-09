"""Shared-module vendoring. Standard library only.

Canonical modules live in cinnamon-tools/shared/<Name>.js; applets that use one
embed a verbatim copy at files/<uuid>/modules/shared/<Name>.js, because
nothing under <uuid>/ may depend on anything outside it. Which modules an
applet embeds is declared in <applet>/tests/harness.json under the
key "shared": ["Spawn", "DBusProxy", ...].

Header convention
  canonical  line 1:  // @shared <Name>.js
  copy       line 1:  // @shared <Name>.js <sha256 of the canonical body> — vendored copy, do not edit; run cinnamon-tools/tools/sync_shared.py
The body (everything after line 1) is byte-identical between the two. The
hash on the copy records which canonical revision it came from, so drift
messages can say "copy from abc1234, canonical now def5678".

Used by tools/sync_shared.py (write copies), tools/check.py (warn on drift)
and harness/run.py (stage a fake applet tree to run cinnamon-tools/tests/shared/).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .applet import TOOLS, Applet, load_json

SHARED_DIR = TOOLS / "shared"
COPY_SUBDIR = Path("modules") / "shared"
HEADER_RE = re.compile(r"^// @shared ([A-Za-z0-9_]+)\.js(?: ([0-9a-f]{64}))?(?: .*)?$")
COPY_NOTE = "vendored copy, do not edit; run cinnamon-tools/tools/sync_shared.py"


class SharedError(Exception):
    pass


def split_header(text: str) -> tuple[str | None, str | None, str]:
    """(name, hash-or-None, body) for a file's text; name None if no header."""
    first, _, body = text.partition("\n")
    m = HEADER_RE.match(first)
    if not m:
        return None, None, text
    return m.group(1), m.group(2), body


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_names() -> list[str]:
    """Module names (without .js) that exist in cinnamon-tools/shared/, sorted."""
    if not SHARED_DIR.is_dir():
        return []
    return sorted(p.stem for p in SHARED_DIR.glob("*.js"))


def canonical_body(name: str) -> tuple[str, str]:
    """(body, hash) of cinnamon-tools/shared/<name>.js; raises SharedError if missing or
    the file lacks the header (the header is how a stray file is told apart)."""
    p = SHARED_DIR / f"{name}.js"
    if not p.exists():
        raise SharedError(f"cinnamon-tools/shared/{name}.js does not exist")
    text = p.read_text(encoding="utf-8")
    hname, _, body = split_header(text)
    if hname != name:
        raise SharedError(f"cinnamon-tools/shared/{name}.js: first line must be '// @shared {name}.js'")
    if not text.endswith("\n"):
        raise SharedError(f"cinnamon-tools/shared/{name}.js: must end with a newline")
    return body, body_hash(body)


def render_copy(name: str, body: str, h: str) -> str:
    return f"// @shared {name}.js {h} — {COPY_NOTE}\n{body}"


def declared_modules(app: Applet) -> list[str]:
    """The 'shared' list from the applet's harness.json (missing key → [])."""
    spec = load_json(app.spec_path)
    if not isinstance(spec, dict):
        return []
    lst = spec.get("shared", [])
    if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
        raise SharedError(f"{app.uuid}: harness.json 'shared' must be a list of module names")
    return [x[:-3] if x.endswith(".js") else x for x in lst]


def copy_path(app_dir: Path, name: str) -> Path:
    return app_dir / COPY_SUBDIR / f"{name}.js"


def inspect_copy(path: Path) -> dict:
    """Compare one embedded copy with its canonical file.
    Returns {name, state, copyHash, canonicalHash, message} where state is one of
    'ok', 'drift' (body differs), 'stale' (body identical but header hash old),
    'no-header', 'no-canonical'."""
    text = path.read_text(encoding="utf-8")
    name, h, body = split_header(text)
    if name is None:
        return {"name": path.stem, "state": "no-header", "message": "no '// @shared' header; not a vendored copy (delete it or move it out of modules/shared/)"}
    try:
        cbody, chash = canonical_body(name)
    except SharedError as e:
        return {"name": name, "state": "no-canonical", "copyHash": h, "message": str(e)}
    bhash = body_hash(body)
    if bhash != chash:
        if h == chash:
            why = f"the copy was edited locally (canonical {chash[:7]} unchanged)"
        else:
            why = f"copy from {(h or '')[:7] or '?'}, canonical now {chash[:7]}"
        return {"name": name, "state": "drift", "copyHash": h, "canonicalHash": chash,
                "message": f"differs from cinnamon-tools/shared/{name}.js ({why}); never edit a copy — fix the canonical file and run sync_shared.py"}
    if h != chash:
        return {"name": name, "state": "stale", "copyHash": h, "canonicalHash": chash,
                "message": f"body matches cinnamon-tools/shared/{name}.js but the header hash is {(h or '')[:7] or '?'}; run sync_shared.py to refresh it"}
    return {"name": name, "state": "ok", "copyHash": h, "canonicalHash": chash, "message": ""}


def sync_applet(app: Applet, write: bool = True) -> list[dict]:
    """Bring every declared module up to date in files/<uuid>/modules/shared/.
    Returns one record per declared module: {name, action} with action
    'unchanged' | 'updated' | 'created' (or 'would-update' / 'would-create'
    when write is False). Raises SharedError for a declared module that has
    no canonical file."""
    out = []
    for name in declared_modules(app):
        body, h = canonical_body(name)
        target = copy_path(app.applet_dir, name)
        wanted = render_copy(name, body, h)
        if target.exists():
            # Same body and hash is up to date, whatever the note after the hash
            # says (older copies name an older path to this tool), so a sync
            # never rewrites shipped files for a comment alone.
            cname, chash, cbody = split_header(target.read_text(encoding="utf-8"))
            action = "unchanged" if (cname == name and chash == h and cbody == body) else "updated"
        else:
            action = "created"
        if action != "unchanged":
            if write:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(wanted, encoding="utf-8")
            else:
                action = "would-" + action.replace("updated", "update").replace("created", "create")
        out.append({"name": name, "action": action, "path": target})
    return out


def stray_copies(app: Applet) -> list[Path]:
    """Files in modules/shared/ that are not declared in harness.json."""
    d = app.applet_dir / COPY_SUBDIR
    if not d.is_dir():
        return []
    declared = set(declared_modules(app))
    return sorted(p for p in d.glob("*.js") if p.stem not in declared)


def stage(dst: Path, names: list[str] | None = None) -> list[Path]:
    """Write vendored copies of the given (default: all) canonical modules into
    dst/modules/shared/, the way they sit inside an applet, so the headless
    suites in cinnamon-tools/tests/shared/ load them through the same require() paths
    an applet uses. dst is wiped first."""
    import shutil
    if dst.exists():
        shutil.rmtree(dst)
    (dst / COPY_SUBDIR).mkdir(parents=True)
    written = []
    for name in names or canonical_names():
        body, h = canonical_body(name)
        p = copy_path(dst, name)
        p.write_text(render_copy(name, body, h), encoding="utf-8")
        written.append(p)
    return written
