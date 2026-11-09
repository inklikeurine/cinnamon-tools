"""Shared helpers for cinnamon-tools: constants, applet discovery, metadata
loading, syntax/safety scans, tree hashing. Standard library only. Used by
check.py, new_applet.py, package.py, on_edit.py, pr_guards.py, fail_first.py
and harness/run.py.

Layout (README.md → "Layout"): these tools are ~/src/cinnamon/cinnamon-tools/
and every applet is its own git repo beside them, ~/src/cinnamon/<name>/,
whose root mirrors a Spices <uuid>/ folder (info.json, README.md,
CHANGELOG.md, screenshot.png, files/<uuid>/) plus its tests (tests/) and the
repo files (AGENTS.md, CLAUDE.md, UNLICENSE, .gitignore, .claude/, .github/).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import zlib
from pathlib import Path

# --- Constants (AGENTS.md → "Hosting and URLs") ---------------------------
OWNER = "inklikeurine"
REPO_PREFIX = "cinnamon-"          # applet <name> lives in github.com/<OWNER>/cinnamon-<name>
TOOLS_REPO = "cinnamon-tools"
BRANCH = "main"
AUTHOR = OWNER
UUID_SUFFIX = f"@{AUTHOR}"
MIN_CINNAMON = (6, 6)
CINNAMON_VERSION_LIST = ["6.6"]
NEW_APPLET_VERSION = "0.1.0"

# These tools are two levels above tools/lib/; applet repos are their siblings.
TOOLS = Path(__file__).resolve().parents[2]
GROUP_ROOT = TOOLS.parent
PYTHON = "/usr/bin/python3"
USER_APPLETS_DIR = Path.home() / ".local/share/cinnamon/applets"

# Cinnamon Settings flags an xlet as "may freeze Cinnamon" on a plain substring
# hit of any of these in any .js file (cinnamon-settings/bin/ExtensionCore.py).
DANGEROUS_SUBSTRINGS = ["spawn_sync", "spawn_command_line_sync", "GTop", "get_file_contents_utf8_sync"]

# settings-schema.json widget types accepted by Cinnamon 6.6 (xlet-settings.py).
WIDGET_TYPES = {
    "entry", "textview", "switch", "spinbutton", "filechooser", "scale", "combobox",
    "colorchooser", "fontchooser", "soundfilechooser", "iconfilechooser", "datechooser",
    "timechooser", "keybinding", "list",
}
DEPRECATED_WIDGET_TYPES = {"checkbox": "switch", "radiogroup": "combobox"}
LAYOUT_TYPES = {"layout", "page", "section", "header", "button", "label", "generic"}
VALUE_TYPES = WIDGET_TYPES | set(DEPRECATED_WIDGET_TYPES) | {"generic"}
ROLES = {"notifications", "windowlist", "windowattentionhandler", "panellauncher", "tray"}

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UUID_RE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)@" + re.escape(AUTHOR) + r"$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
CINNAMON_VER_RE = re.compile(r"^\d+\.\d+$")
CHANGELOG_HEAD_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]\s*[—–-]\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)

# Entries in the group folder (~/src/cinnamon/) that are not applet repos:
# these tools, the maintainer's private workshop, bug write-ups, and
# snap-layouts, a Cinnamon extension still at the planning stage.
NOT_APPLETS = {"_workshop", "bugs", "snap-layouts", "cinnamon-snap-layouts", TOOLS_REPO, "cinnamon-workshop", TOOLS.name}
# Entries an applet repo has besides the Spices <uuid>/ entries: its tests and
# the repo files. None of them ever goes to Spices.
REPO_FILES = {"AGENTS.md", "CLAUDE.md", "UNLICENSE", ".gitignore", ".claude", ".github", ".git"}
TESTS_DIR = "tests"
# The five entries a Spices <uuid>/ folder is built from (package.py --spices).
SPICES_ENTRIES = ["info.json", "README.md", "CHANGELOG.md", "screenshot.png", "files"]

README_SECTIONS = [
    ("Features", re.compile(r"^##\s+features\s*$", re.I | re.M)),
    ("Requirements", re.compile(r"^##\s+requirements\s*$", re.I | re.M)),
    ("Install", re.compile(r"^##\s+install(ation)?\s*$", re.I | re.M)),
    ("Usage", re.compile(r"^##\s+usage\s*$", re.I | re.M)),
    ("Notes / caveats", re.compile(r"^##\s+notes\s*/\s*caveats\s*$", re.I | re.M)),
    ("AI-generated", re.compile(r"^##\s+ai-generated\s*$", re.I | re.M)),
    ("License", re.compile(r"^##\s+license\s*$", re.I | re.M)),
]


def repo_name(uuid: str) -> str:
    """'wallshift@inklikeurine' -> 'cinnamon-wallshift'."""
    return REPO_PREFIX + uuid_name(uuid)


def repo_url(uuid: str) -> str:
    return f"https://github.com/{OWNER}/{repo_name(uuid)}"


def source_url(uuid: str) -> str:
    """The README "Source" link: the applet's own repository."""
    return repo_url(uuid)


def uuid_name(uuid: str) -> str:
    """'wallshift@inklikeurine' -> 'wallshift'."""
    return uuid.split("@", 1)[0]


def applet_dir_of(name: str) -> Path:
    """The applet's repo folder: <name>/, or cinnamon-<name>/ as a plain
    `git clone` names it."""
    for cand in (GROUP_ROOT / name, GROUP_ROOT / (REPO_PREFIX + name)):
        if cand.is_dir():
            return cand
    return GROUP_ROOT / name


def name_of_folder(folder: str) -> str:
    """'cinnamon-hush' -> 'hush'; 'hush' -> 'hush'."""
    return folder[len(REPO_PREFIX):] if folder.startswith(REPO_PREFIX) else folder


# --- Applet discovery -------------------------------------------------------
class Applet:
    def __init__(self, uuid: str):
        self.uuid = uuid
        self.dir = applet_dir_of(uuid_name(uuid))   # the applet's own git repo
        self.files_dir = self.dir / "files"
        self.applet_dir = self.files_dir / uuid
        self.info_path = self.dir / "info.json"
        self.readme_path = self.dir / "README.md"
        self.changelog_path = self.dir / "CHANGELOG.md"
        self.screenshot_path = self.dir / "screenshot.png"
        self.metadata_path = self.applet_dir / "metadata.json"
        self.applet_js = self.applet_dir / "applet.js"
        self.icon_path = self.applet_dir / "icon.png"
        self.schema_path = self.applet_dir / "settings-schema.json"
        self.tests_dir = self.dir / TESTS_DIR
        self.spec_path = self.tests_dir / "harness.json"   # live-check settings and shared modules
        self.out_dir = TOOLS / "out" / uuid                # harness output; ignored by git

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(GROUP_ROOT))
        except ValueError:
            return str(p)

    def metadata(self) -> dict | None:
        return load_json(self.metadata_path)

    def version(self) -> str | None:
        m = self.metadata()
        return m.get("version") if isinstance(m, dict) else None

    def name(self) -> str | None:
        m = self.metadata()
        return m.get("name") if isinstance(m, dict) else None


def is_applet_repo(d: Path) -> bool:
    """A folder in the group root that holds an applet: it has files/ or info.json."""
    return d.is_dir() and not d.name.startswith((".", "_")) and d.name not in NOT_APPLETS \
        and ((d / "files").is_dir() or (d / "info.json").exists())


def discover_applets() -> list[Applet]:
    """Every applet repo beside these tools, sorted by UUID."""
    names = sorted({name_of_folder(ent.name) for ent in GROUP_ROOT.iterdir() if is_applet_repo(ent)})
    return [Applet(n + UUID_SUFFIX) for n in names]


def find_applet(arg: str) -> Applet | None:
    """An applet by UUID ('hush@inklikeurine') or short name ('hush')."""
    uuid = arg if "@" in arg else arg + UUID_SUFFIX
    return next((a for a in discover_applets() if a.uuid == uuid), None)


def load_json(path: Path):
    """Parsed JSON, or None if the file is missing or invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Scans ----------------------------------------------------------------
_COMMENT_STRING_RE = re.compile(
    r"/\*[\s\S]*?\*/"                      # block comments
    r"|(?<![:\\])//[^\n]*"                 # line comments (not inside http://)
    r"|`(?:\\[\s\S]|[^`\\])*`"             # template literals
    r"|'(?:\\.|[^'\\\n])*'"                # single-quoted strings
    r"|\"(?:\\.|[^\"\\\n])*\"",            # double-quoted strings
)


def strip_comments_and_strings(src: str) -> str:
    """Crude but adequate for identifier scans; not a tokenizer."""
    return _COMMENT_STRING_RE.sub(" ", src)


def dangerous_hits(src: str) -> list[str]:
    """Substrings Cinnamon's settings dialog searches for, raw (comments count)."""
    return [s for s in DANGEROUS_SUBSTRINGS if s in src]


_CJS_PARSE = (
    "const GLib = imports.gi.GLib;"
    "let [ok, c] = GLib.file_get_contents(ARGV[0]);"
    "try { new Function(new TextDecoder().decode(c)); print('OK'); }"
    "catch (e) { print('ERR ' + (e.lineNumber || '?') + ': ' + e.message); }"
)


def cjs_parse_error(path: Path) -> str | None:
    """Parse a JS file with Cinnamon's own engine (cjs) without executing it.
    Returns None on success, an error string on failure, or a note if cjs is
    missing (caller decides whether that is fatal)."""
    try:
        res = subprocess.run(["cjs", "-c", _CJS_PARSE, str(path)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "cjs not found (install cinnamon)"
    except subprocess.TimeoutExpired:
        return "cjs timed out"
    out = (res.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if last == "OK":
        return None
    if last.startswith("ERR "):
        return "syntax " + last[4:]
    return (res.stderr or last or "cjs failed").strip().splitlines()[-1]


def py_compile_error(path: Path) -> str | None:
    """Compile-check a Python file without writing bytecode (py_compile would
    drop a __pycache__ next to the source, which the layout rules forbid)."""
    res = subprocess.run(
        [PYTHON, "-B", "-c", "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')", str(path)],
        capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if res.returncode == 0:
        return None
    lines = (res.stderr or "py_compile failed").strip().splitlines()
    return " | ".join(lines[-3:])


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from the IHDR chunk, or None if not a PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def has_non_ascii(obj) -> bool:
    return any(ord(ch) > 127 for ch in json.dumps(obj, ensure_ascii=False))


# --- Versions -----------------------------------------------------------------
def parse_semver(v: str) -> tuple[int, int, int] | None:
    m = SEMVER_RE.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def latest_release_tag(repo: Path) -> str | None:
    """The highest local v<x.y.z> tag, without the v, or None when the repo
    has none (`git fetch --tags` brings them here)."""
    try:
        res = subprocess.run(["git", "tag", "--list", "v*"], cwd=repo, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    versions = [t.strip()[1:] for t in res.stdout.splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t.strip())]
    return max(versions, key=parse_semver) if versions else None


def is_shipped_path(rel: str) -> bool:
    """True for a path (relative to the applet repo root) inside files/<uuid>/:
    the code that gets installed. README, CHANGELOG, screenshot, tests and the
    repo files are not shipped code."""
    return rel.split("/", 1)[0] == "files"


def parse_cinnamon_version(v: str) -> tuple[int, int] | None:
    m = CINNAMON_VER_RE.match(v or "")
    if not m:
        return None
    a, b = v.split(".")
    return int(a), int(b)


# --- Tree hash (Automated Test evidence) --------------------------------------
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}


def tree_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.endswith((".pyc", "~", ".swp")):
                continue
            out.append(Path(dirpath) / fn)
    return out


def tree_sha256(root: Path) -> str:
    """sha256 over (relative path, contents) of every file, sorted; what
    result.json records so a later edit invalidates an Automated Test tick."""
    h = hashlib.sha256()
    for p in tree_files(root):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# --- Placeholder icon -----------------------------------------------------------
def placeholder_icon_png(size: int = 64) -> bytes:
    """A deterministic square PNG (grey disc on transparent) so a new applet
    has a valid icon.png until a real one is drawn. check.py warns while the
    file still matches these bytes."""
    rows = []
    c = size / 2 - 0.5
    r2 = (size * 0.42) ** 2
    for y in range(size):
        row = bytearray([0])  # filter type 0
        for x in range(size):
            inside = (x - c) ** 2 + (y - c) ** 2 <= r2
            row += bytes([0x60, 0x60, 0x60, 0xFF]) if inside else bytes([0, 0, 0, 0])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


# --- Findings --------------------------------------------------------------------
class Findings:
    """Collects (level, file, rule, message); errors fail a run, warnings never do.
    Spices-only rules use level 'spices' and print as warnings unless promoted."""

    def __init__(self, promote_spices: bool = False):
        self.items: list[tuple[str, str, str, str]] = []
        self.promote_spices = promote_spices

    def error(self, file: str, rule: str, msg: str):
        self.items.append(("error", file, rule, msg))

    def warn(self, file: str, rule: str, msg: str):
        self.items.append(("warning", file, rule, msg))

    def spices(self, file: str, rule: str, msg: str):
        self.items.append(("error" if self.promote_spices else "spices", file, rule, msg))

    @property
    def errors(self) -> int:
        return sum(1 for i in self.items if i[0] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.items if i[0] != "error")

    def render(self, quiet: bool = False) -> list[str]:
        order = {"error": 0, "spices": 1, "warning": 2}
        lines = []
        for level, file, rule, msg in sorted(self.items, key=lambda i: (order[i[0]], i[1], i[2])):
            if quiet and level != "error":
                continue
            tag = "" if level == "error" else ("spices: " if level == "spices" else "warning: ")
            lines.append(f"{file}: {tag}{rule}: {msg}")
        return lines
