#!/usr/bin/python3
"""Validator for every mechanical rule in an applet's AGENTS.md. Standard
library only.

    python3 ../cinnamon-tools/tools/check.py [--quiet] [--spices] [--uuid <uuid|name>]
    python3 ../cinnamon-tools/tools/check.py <name>

Checks every applet repo beside cinnamon-tools (~/src/cinnamon/<name>/), or
the one named.

Prints one line per finding, "path: [level: ]rule: message", and exits 1 if
any finding is an error. Warnings never fail the run. Spices-submission rules
print as "spices:" warnings; --spices promotes them to errors (package.py does
this). --no-journal is accepted and ignored (the journal check is gone: the
pull request checks are the evidence now).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.applet import (  # noqa: E402
    AUTHOR, CINNAMON_VERSION_LIST, CHANGELOG_HEAD_RE, DEPRECATED_WIDGET_TYPES, LAYOUT_TYPES,
    TOOLS, MIN_CINNAMON, NAME_RE, NOT_APPLETS, README_SECTIONS, GROUP_ROOT, REPO_FILES, ROLES,
    TESTS_DIR, UUID_RE, UUID_SUFFIX, VALUE_TYPES, WIDGET_TYPES,
    Applet, Findings, cjs_parse_error, dangerous_hits, discover_applets, find_applet, has_non_ascii,
    latest_release_tag, load_json, name_of_folder, parse_cinnamon_version, parse_semver, placeholder_icon_png,
    png_size, py_compile_error, source_url, strip_comments_and_strings,
)
from lib.shared import COPY_SUBDIR, SharedError, declared_modules, inspect_copy, stray_copies  # noqa: E402

args = sys.argv[1:]
QUIET = "--quiet" in args
ONLY = args[args.index("--uuid") + 1] if "--uuid" in args else next((a for a in args if not a.startswith("-")), None)
F = Findings(promote_spices="--spices" in args)

UNLICENSE_TEXT = (TOOLS / "templates" / "UNLICENSE").read_text() if (TOOLS / "templates" / "UNLICENSE").exists() else None
ALLOWED_TOP = {"info.json", "README.md", "CHANGELOG.md", "screenshot.png", "files", TESTS_DIR}   # the Spices <uuid>/ entries + tests/
PLACEHOLDER_RE = re.compile(r"__(NAME|SHORTNAME|DESCRIPTION|UUID|CLASS|DATE|VERSION)__")
FORBIDDEN_META = ["icon", "dangerous", "last-edited"]
REQUIRED_META = ["uuid", "name", "description", "version", "cinnamon-version", "max-instances"]
KNOWN_META = set(REQUIRED_META) | {"hide-configuration", "external-configuration-app", "role", "url", "multiversion"}


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(GROUP_ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# The group folder and each applet's repo files
# ---------------------------------------------------------------------------
def check_group():
    """~/src/cinnamon/ holds applet repos, cinnamon-tools, the workshop and the bugs/ notes."""
    for ent in sorted(GROUP_ROOT.iterdir()):
        if ent.name.startswith(".") or ent.name in NOT_APPLETS:
            continue
        if not ent.is_dir():
            F.warn(ent.name, "layout", f"stray file in {GROUP_ROOT}; only applet repos, cinnamon-tools/, _workshop/ and bugs/ belong here")
        elif not ((ent / "files").is_dir() or (ent / "info.json").exists()):
            F.warn(ent.name, "layout", "folder is neither an applet repo (files/ or info.json) nor cinnamon-tools/, _workshop/ or bugs/")


def check_repo(a: Applet):
    """The repo-level files every applet repo carries (AGENTS.md → "Required repo files")."""
    for name in ["AGENTS.md", "CLAUDE.md", "UNLICENSE", ".gitignore"]:
        if not (a.dir / name).exists():
            F.error(rel(a.dir / name), "repo-files", "required file is missing")
    if not (a.dir / ".git").exists():
        F.error(rel(a.dir), "repo-files", "not a git repository; every applet is its own repo")
    lic = a.dir / "UNLICENSE"
    if lic.exists() and UNLICENSE_TEXT:
        norm = lambda s: re.sub(r"[ \t]+$", "", s.replace("\r\n", "\n"), flags=re.M).strip()
        if norm(lic.read_text()) != norm(UNLICENSE_TEXT):
            F.error(rel(lic), "unlicense-verbatim", "text differs from the canonical Unlicense (cinnamon-tools/templates/UNLICENSE)")
    if (a.dir / "LICENSE").exists():
        F.error(rel(a.dir / "LICENSE"), "unlicense-name", "the license file is named UNLICENSE; remove LICENSE")
    agents = a.dir / "AGENTS.md"
    if agents.exists():
        text = agents.read_text(encoding="utf-8")
        if re.search(r"^@\S", text, re.M):
            F.error(rel(agents), "agents-md", "must be self-contained: no @ import lines (generate it from cinnamon-tools/templates/repo/AGENTS.md)")
        if PLACEHOLDER_RE.search(text):
            F.error(rel(agents), "placeholder", "template placeholder (__X__) left in file")
    claude = a.dir / "CLAUDE.md"
    if claude.exists() and claude.read_text(encoding="utf-8").strip() != "@AGENTS.md":
        F.error(rel(claude), "repo-files", "must be the single line @AGENTS.md")
    st_path = a.dir / ".claude" / "settings.json"
    if not st_path.exists():
        F.error(rel(st_path), "repo-files", "missing; it wires the validator hook (copy cinnamon-tools/templates/repo/claude-settings.json)")
    else:
        st = load_json(st_path)
        if not isinstance(st, dict):
            F.error(rel(st_path), "hook", "not valid JSON")
        elif "on_edit.py" not in json.dumps(st.get("hooks", {})):
            F.error(rel(st_path), "hook", "PostToolUse hook must run cinnamon-tools/tools/on_edit.py")
    for f in ["package.json", "package-lock.json", ".eslintrc", ".eslintrc.json", "eslint.config.js", ".prettierrc",
              "tsconfig.json", "pyproject.toml", "setup.py", "requirements.txt", "Makefile"]:
        if (a.dir / f).exists():
            F.error(rel(a.dir / f), "no-tooling-at-root", "AGENTS.md forbids build/lint config in an applet repo; tooling lives in cinnamon-tools/")
    wf = a.dir / ".github" / "workflows"
    ci = wf / "checks.yml"
    if not ci.exists():
        F.error(rel(ci), "ci", "the pull request checks are required (copy cinnamon-tools/templates/repo/checks.yml)")
    elif "check.py" not in ci.read_text(encoding="utf-8"):
        F.error(rel(ci), "ci", "workflow must run cinnamon-tools/tools/check.py")
    if not (wf / "guards.yml").exists():
        F.warn(rel(wf / "guards.yml"), "ci", "the pull request guards are missing (copy cinnamon-tools/templates/repo/guards.yml)")


# ---------------------------------------------------------------------------
# Per-applet
# ---------------------------------------------------------------------------
def check_applet(a: Applet) -> dict | None:
    u = a.uuid
    m = UUID_RE.match(u)
    if not m:
        F.error(u, "uuid", f"must be <name>{UUID_SUFFIX} with a lowercase, hyphen-separated name")
    else:
        name = m.group(1)
        if not NAME_RE.match(name):
            F.error(u, "uuid", "name part must be lowercase letters, digits and hyphens")
        if name.endswith("-applet") or name == "applet":
            F.error(u, "uuid", "drop the 'applet' suffix; every folder here is an applet")

    # The repo holds exactly the Spices <uuid>/ entries plus the repo files.
    if not a.dir.is_dir():
        F.error(rel(a.dir), "layout", "applet repo folder is missing")
        return None
    check_repo(a)
    for ent in a.dir.iterdir():
        if ent.name not in ALLOWED_TOP and ent.name not in REPO_FILES and ent.name != ".github":
            if ent.name == "icon.png":
                F.spices(rel(ent), "layout", "forbidden here; icon.png belongs in files/<uuid>/")
            elif ent.name.endswith((".po", ".pot")):
                F.spices(rel(ent), "translations", "translation files belong in files/<uuid>/po/")
            else:
                F.error(rel(ent), "layout", "only info.json, README.md, CHANGELOG.md, screenshot.png, files/, tests/ and the repo files may be in an applet repo")
    for f in [a.info_path, a.readme_path, a.changelog_path]:
        if not f.exists():
            F.error(rel(f), "layout", "required file is missing")
    if not a.screenshot_path.exists():
        F.spices(rel(a.screenshot_path), "screenshot", "missing; Spices requires a screenshot.png")
    elif png_size(a.screenshot_path) is None:
        F.spices(rel(a.screenshot_path), "screenshot", "not a PNG")

    # files/ holds exactly <uuid>/.
    if not a.files_dir.is_dir():
        F.error(rel(a.files_dir), "layout", "missing files/ directory")
        return None
    kids = [k.name for k in a.files_dir.iterdir()]
    if kids != [u]:
        F.error(rel(a.files_dir), "layout", f"files/ must contain only {u}/ (found: {', '.join(kids) or 'nothing'})")
    if not a.applet_dir.is_dir():
        return None

    # Nothing regenerable or foreign inside files/<uuid>/.
    for p in a.applet_dir.rglob("*"):
        n = p.name
        if p.is_dir() and n in ("__pycache__", "node_modules", ".git"):
            F.error(rel(p), "layout", "must not be inside files/<uuid>/; delete it")
        elif p.is_file():
            if n.endswith((".pyc", ".mo")):
                F.error(rel(p), "layout", "compiled file inside files/<uuid>/; delete it")
            elif n.endswith((".zip", ".tar", ".tar.gz", ".tgz", ".deb")):
                F.error(rel(p), "layout", "archive inside files/<uuid>/")
            elif n.endswith((".po", ".pot")) and p.parent != a.applet_dir / "po":
                F.spices(rel(p), "translations", "translation files belong in files/<uuid>/po/")
    pots = list((a.applet_dir / "po").glob("*.pot")) if (a.applet_dir / "po").is_dir() else []
    if len(pots) > 1:
        F.spices(rel(a.applet_dir / "po"), "translations", "more than one .pot file")

    meta = check_metadata(a)
    check_info(a)
    check_js(a, meta)
    check_shared(a)
    check_python(a)
    check_icon(a)
    check_schema(a)
    check_readme(a, meta)
    check_changelog(a, meta)
    check_version(a, meta)
    check_tests(a)
    return meta


def check_metadata(a: Applet) -> dict | None:
    f = rel(a.metadata_path)
    if not a.metadata_path.exists():
        F.error(f, "metadata", "missing")
        return None
    meta = load_json(a.metadata_path)
    if not isinstance(meta, dict):
        F.error(f, "metadata", "not a JSON object")
        return None
    for k in REQUIRED_META:
        if k not in meta:
            F.error(f, "metadata", f"missing key '{k}'")
    for k in FORBIDDEN_META:
        if k in meta:
            F.spices(f, "metadata", f"Spices forbids '{k}'" + (" — ship files/<uuid>/icon.png instead" if k == "icon" else ""))
    for k in meta:
        if k not in KNOWN_META and k not in FORBIDDEN_META:
            F.warn(f, "metadata", f"unknown key '{k}'; explain it in the README or drop it")
    if meta.get("uuid") != a.uuid:
        F.error(f, "metadata", f"uuid '{meta.get('uuid')}' must equal the folder name '{a.uuid}'")
    if not isinstance(meta.get("name"), str) or not meta.get("name", "").strip():
        F.error(f, "metadata", "name must be a non-empty string")
    if not isinstance(meta.get("description"), str) or not meta.get("description", "").strip():
        F.error(f, "metadata", "description must be a non-empty string")
    v = meta.get("version")
    if "version" in meta and (not isinstance(v, str) or not parse_semver(v)):
        F.error(f, "version", f"version '{v}' must be semver MAJOR.MINOR.PATCH (e.g. 0.1.0)")
    cv = meta.get("cinnamon-version")
    if "cinnamon-version" in meta:
        if not isinstance(cv, list) or not cv or not all(isinstance(x, str) and parse_cinnamon_version(x) for x in cv):
            F.error(f, "cinnamon-version", 'must be a non-empty list of "MAJOR.MINOR" strings, e.g. ["6.6"]')
        else:
            lowest = min(parse_cinnamon_version(x) for x in cv)
            if lowest > MIN_CINNAMON:
                F.error(f, "cinnamon-version", f"lowest entry {'.'.join(map(str, lowest))} is above 6.6; the applet would not load on Cinnamon 6.6")
            elif cv != CINNAMON_VERSION_LIST:
                F.warn(f, "cinnamon-version", f"declares {cv}; the repo targets 6.6+ only — set it to {CINNAMON_VERSION_LIST} at the next version bump")
    mi = meta.get("max-instances")
    if "max-instances" in meta and (not isinstance(mi, int) or isinstance(mi, bool) or mi == 0 or mi < -1):
        F.error(f, "metadata", "max-instances must be -1 (unlimited) or a positive integer")
    if "role" in meta and meta["role"] not in ROLES:
        F.error(f, "metadata", f"role '{meta['role']}' is not one of {sorted(ROLES)}")
    if "hide-configuration" in meta and not isinstance(meta["hide-configuration"], bool):
        F.error(f, "metadata", "hide-configuration must be true or false")
    if meta.get("hide-configuration") is True and a.schema_path.exists():
        F.warn(f, "metadata", "hide-configuration is true but settings-schema.json exists; users cannot reach the settings")
    if "external-configuration-app" in meta and not (a.applet_dir / str(meta["external-configuration-app"])).exists():
        F.error(f, "metadata", f"external-configuration-app '{meta['external-configuration-app']}' does not exist in files/<uuid>/")
    if "url" in meta and meta["url"] != source_url(a.uuid):
        F.error(f, "metadata", f"url, if present, must be {source_url(a.uuid)}")
    if has_non_ascii(meta):
        F.spices(f, "metadata", "contains non-ASCII characters; Spices rejects them")
    return meta


def check_info(a: Applet):
    f = rel(a.info_path)
    if not a.info_path.exists():
        return
    info = load_json(a.info_path)
    if not isinstance(info, dict):
        F.error(f, "info", "not a JSON object")
        return
    if info.get("author") != AUTHOR:
        F.error(f, "info", f'author must be "{AUTHOR}"')
    if any(ch.isspace() for ch in str(info.get("author", ""))):
        F.spices(f, "info", "whitespace in author")
    for k in info:
        if k != "author":
            F.warn(f, "info", f"unexpected key '{k}'")


def check_js(a: Applet, meta: dict | None):
    if not a.applet_js.exists():
        F.error(rel(a.applet_js), "layout", "missing applet.js")
    for p in sorted(a.applet_dir.rglob("*.js")):
        f = rel(p)
        src = p.read_text(encoding="utf-8", errors="replace")
        for hit in dangerous_hits(src):
            F.error(f, "dangerous", f"contains '{hit}'; Cinnamon Settings flags the applet as one that may freeze Cinnamon (substring match, comments included)")
        err = cjs_parse_error(p)
        if err:
            (F.warn if err.startswith("cjs not found") else F.error)(f, "syntax", err)
        code = strip_comments_and_strings(src)
        if not src.endswith("\n"):
            F.error(f, "loader", "file must end with a newline: Cinnamon's loader appends ';' and the export lines to the source, and a trailing line comment would swallow them")
        # Cinnamon evaluates applet.js (and anything it require()s) through its own
        # loader, which provides require(<path relative to the applet dir> | gi.X | ui.X | misc.X).
        # The GJS importer route (imports.searchPath + imports.<dir>.X) caches modules
        # for the life of the process, so a reload runs new applet.js against old modules.
        if re.search(r"\bimports\.searchPath\b", code):
            F.error(f, "loader", "imports.searchPath: local modules loaded through the GJS importer are never reloaded; use require('./modules/<Name>')")
        code_keep_strings = re.sub(r"/\*[\s\S]*?\*/|(?<![:\\])//[^\n]*", " ", src)   # comments out, strings kept
        for m in re.finditer(r"\brequire\s*\(\s*([^)]*)\)", code_keep_strings):
            arg = m.group(1).strip()
            lit = re.fullmatch(r"(['\"])(.+?)\1", arg)
            if not lit:
                F.warn(f, "loader", f"require({arg}): argument is not a string literal; the validator cannot check the target")
                continue
            target = lit.group(2)
            if target.startswith(("gi.", "ui.", "misc.", "perf.")):
                continue
            if target.startswith("/"):
                F.error(f, "loader", f"require('{target}'): absolute path; use a path relative to the applet dir")
            elif "/" not in target and not target.startswith("."):
                F.error(f, "loader", f"require('{target}'): bare module names are Node, not Cinnamon; use './modules/<Name>', 'gi.<Ns>' or 'ui.<module>'")
            else:
                rel_t = target.replace("./", "")
                if not rel_t.endswith(".js"):
                    rel_t += ".js"
                if not (a.applet_dir / rel_t).exists():
                    F.error(f, "loader", f"require('{target}'): {rel_t} does not exist under files/<uuid>/ (paths are relative to the applet dir, even from inside modules/)")
        if re.search(r"\bconsole\.log\s*\(", code):
            F.warn(f, "style", "console.log left in; use global.log (and only for warnings/errors)")
        if re.search(r"\b(setTimeout|setInterval)\s*\(", code):
            F.error(f, "cjs", "setTimeout/setInterval are not available in CJS; use GLib.timeout_add[_seconds]")
        if re.search(r"\bLang\.Class\b|\.prototype\s*=\s*\{", code):
            F.warn(f, "style", "prototype-style class; use an ES6 class")
        if p == a.applet_js:
            if not re.search(r"\bfunction\s+main\s*\(", code):
                F.error(f, "entry", "applet.js must define function main(metadata, orientation, panelHeight, instanceId)")
            if not re.search(r"\bclass\s+\w+\s+extends\s+Applet\.(Applet|IconApplet|TextApplet|TextIconApplet)\b", code):
                F.error(f, "entry", "applet class must extend Applet.Applet / IconApplet / TextApplet / TextIconApplet")
            if not re.search(r"\bon_applet_removed_from_panel\s*\(", code):
                F.warn(f, "lifecycle", "no on_applet_removed_from_panel(); anything the applet connects, times or spawns must be released there")
            if a.schema_path.exists() and not re.search(r"\bAppletSettings\b", code):
                F.warn(f, "settings", "settings-schema.json exists but applet.js never uses Settings.AppletSettings")
            if re.search(r"\b(python3|python)\s+['\"]?[^'\"\s]*\.py", code) and "/usr/bin/python3" not in src:
                F.warn(f, "python", "spawns python3 by PATH name; use /usr/bin/python3 explicitly (PATH python3 may lack gi)")


def check_shared(a: Applet):
    """Vendored copies of cinnamon-tools/shared/ modules: every
    files/<uuid>/modules/shared/<Name>.js must be declared in
    tests/harness.json 'shared' and match the canonical file
    byte for byte below its header. Warnings only: the copy is what ships and
    is valid on its own; drift just means a fix has not been propagated."""
    d = a.applet_dir / COPY_SUBDIR
    try:
        declared = declared_modules(a)
    except SharedError as e:
        F.warn(rel(a.spec_path), "shared", str(e))
        declared = []
    for name in declared:
        if not (d / f"{name}.js").exists():
            F.warn(rel(d / f"{name}.js"), "shared", f"declared in harness.json but not present; run python3 ../cinnamon-tools/tools/sync_shared.py {a.uuid}")
    if not d.is_dir():
        return
    for p in sorted(d.glob("*.js")):
        info = inspect_copy(p)
        if info["state"] != "ok":
            F.warn(rel(p), "shared", info["message"])
    for p in stray_copies(a):
        F.warn(rel(p), "shared", f"not declared in {rel(a.spec_path)} 'shared'; declare it or move it out of modules/shared/")
    for p in sorted(d.iterdir()):
        if p.is_file() and not p.name.endswith(".js"):
            F.warn(rel(p), "shared", "modules/shared/ holds only vendored .js modules")


def check_python(a: Applet):
    for p in sorted(a.applet_dir.rglob("*.py")):
        f = rel(p)
        err = py_compile_error(p)
        if err:
            F.error(f, "syntax", err)
        first = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        if first.startswith("#!") and first.strip() != "#!/usr/bin/python3":
            F.warn(f, "python", f"shebang '{first.strip()}'; use #!/usr/bin/python3")


def check_icon(a: Applet):
    f = rel(a.icon_path)
    if not a.icon_path.exists():
        F.spices(f, "icon", "missing; Spices requires files/<uuid>/icon.png")
        return
    size = png_size(a.icon_path)
    if size is None:
        F.spices(f, "icon", "not a PNG")
        return
    w, h = size
    if w != h:
        F.spices(f, "icon", f"must be square (is {w}x{h})")
    elif w < 32:
        F.warn(f, "icon", f"{w}x{h} is small; 48x48 or larger renders cleanly in System Settings")
    if a.icon_path.read_bytes() == placeholder_icon_png():
        F.warn(f, "icon", "still the scaffold placeholder; draw a real icon before release")


def check_schema(a: Applet):
    if not a.schema_path.exists():
        return
    f = rel(a.schema_path)
    schema = load_json(a.schema_path)
    if not isinstance(schema, dict):
        F.error(f, "schema", "not valid JSON / not an object")
        return
    for key, spec in schema.items():
        if not isinstance(spec, dict):
            F.error(f, "schema", f"'{key}' must be an object")
            continue
        t = spec.get("type")
        if t is None:
            F.error(f, "schema", f"'{key}' has no type")
            continue
        if t in DEPRECATED_WIDGET_TYPES:
            F.warn(f, "schema", f"'{key}': type '{t}' is deprecated; use '{DEPRECATED_WIDGET_TYPES[t]}'")
        elif t not in WIDGET_TYPES and t not in LAYOUT_TYPES:
            F.error(f, "schema", f"'{key}': unknown type '{t}'")
        if t in VALUE_TYPES and "default" not in spec and t != "generic":
            F.error(f, "schema", f"'{key}' ({t}) has no default")
        if t in WIDGET_TYPES | set(DEPRECATED_WIDGET_TYPES) and not spec.get("description"):
            F.warn(f, "schema", f"'{key}' ({t}) has no description; the dialog shows an unlabeled widget")
        if t == "combobox" and not isinstance(spec.get("options"), dict):
            F.error(f, "schema", f"'{key}': combobox needs an options object")
        if t in ("spinbutton", "scale"):
            for k in ("min", "max"):
                if k not in spec:
                    F.error(f, "schema", f"'{key}': {t} needs {k}")
        if t == "layout":
            # Pages and sections are nested inside the layout object; section
            # "keys" refer to top-level schema keys.
            for page in spec.get("pages", []):
                pg = spec.get(page)
                if not isinstance(pg, dict) or pg.get("type") != "page":
                    F.error(f, "schema", f"layout page '{page}' is not defined inside the layout object")
                    continue
                for sec in pg.get("sections", []):
                    sc = spec.get(sec)
                    if not isinstance(sc, dict) or sc.get("type") != "section":
                        F.error(f, "schema", f"layout section '{sec}' (page '{page}') is not defined inside the layout object")
                        continue
                    for k in sc.get("keys", []):
                        if k not in schema:
                            F.error(f, "schema", f"layout section '{sec}' lists key '{k}' which is not defined")
            laid_out = {k for pg in spec.values() if isinstance(pg, dict) for sc in [spec.get(s) for s in pg.get("sections", [])] if isinstance(sc, dict) for k in sc.get("keys", [])}
            for k, v in schema.items():
                if k != "layout" and isinstance(v, dict) and v.get("type") in WIDGET_TYPES | set(DEPRECATED_WIDGET_TYPES) and k not in laid_out:
                    F.warn(f, "schema", f"'{k}' is not placed in any layout section; it will not appear in the dialog")
        elif t in ("page", "section"):
            F.error(f, "schema", f"'{key}': type '{t}' belongs inside the layout object, not at the top level")


def check_readme(a: Applet, meta: dict | None):
    if not a.readme_path.exists():
        return
    f = rel(a.readme_path)
    md = a.readme_path.read_text(encoding="utf-8")
    if PLACEHOLDER_RE.search(md):
        F.error(f, "placeholder", "template placeholder (__X__) left in file")
    if re.search(r"<!--[\s\S]*?-->", md):
        F.warn(f, "placeholder", "HTML comment left in README (template guidance?)")
    h1 = re.search(r"^#\s+(.+?)\s*$", md, re.M)
    if not h1:
        F.error(f, "readme-sections", "missing title (# heading)")
    elif meta and isinstance(meta.get("name"), str) and h1.group(1).strip() != meta["name"].strip():
        F.error(f, "readme-sections", f'title "{h1.group(1).strip()}" must equal metadata name "{meta["name"].strip()}"')
    positions = []
    for label, rx in README_SECTIONS:
        mm = rx.search(md)
        if not mm:
            F.error(f, "readme-sections", f'missing "## {label}" section')
        else:
            positions.append((label, mm.start()))
    for i in range(1, len(positions)):
        if positions[i][1] < positions[i - 1][1]:
            F.warn(f, "readme-sections", f'sections out of the required order (at "{positions[i][0]}")')
            break
    src = source_url(a.uuid)
    if src not in md:
        F.error(f, "source-link", f"must link to {src}")
    if not re.search(r"cinnamon\s*6\.6|mint\s*22\.3", md, re.I):
        F.error(f, "requirements", "must say Cinnamon 6.6+ / Linux Mint 22.3+")
    if not re.search(r"\bx11\b", md, re.I):
        F.warn(f, "requirements", "should say the applet is for X11 sessions")
    if not re.search(r"at\s+(your|the user'?s)\s+own\s+risk", md, re.I):
        F.error(f, "ai-generated", "AI-generated section must say the applet is used at the user's own risk")
    lic = re.search(r"^##\s+License\s*$([\s\S]*?)(?=^##\s|\Z)", md, re.M | re.I)
    if lic and "(UNLICENSE)" not in lic.group(1):
        F.error(f, "license", '"## License" section must link to UNLICENSE, e.g. [Unlicense](UNLICENSE)')
    if not re.search(r"\.local/share/cinnamon/applets", md):
        F.error(f, "install", "Install section must show where files/<uuid>/ goes (~/.local/share/cinnamon/applets/)")
    code = ""
    for p in a.applet_dir.rglob("*.js"):
        code += strip_comments_and_strings(p.read_text(encoding="utf-8", errors="replace"))
    net = [n for n, rx in [("Soup", r"\bSoup\b"), ("Gio.SocketClient", r"\bSocketClient\b"), ("curl/wget", r"\b(curl|wget)\b")] if re.search(rx, code)]
    if net and not re.search(r"network|request|download|internet", md, re.I):
        F.error(f, "network", f"applet appears to use the network ({', '.join(net)}) but the README never says so")


def check_changelog(a: Applet, meta: dict | None):
    """Newest first, one '## [x.y.z] — YYYY-MM-DD' heading per release, and the
    newest is the current version. The version and the changelog only change
    together, in the maintainer's release pull request."""
    if not a.changelog_path.exists():
        return
    f = rel(a.changelog_path)
    md = a.changelog_path.read_text(encoding="utf-8")
    heads = CHANGELOG_HEAD_RE.findall(md)
    if len(re.findall(r"^##\s+", md, re.M)) != len(heads):
        F.error(f, "changelog", 'every "## " heading must be "## [x.y.z] — YYYY-MM-DD"')
    if not heads:
        F.error(f, "changelog", "no version entries")
        return
    if meta and isinstance(meta.get("version"), str):
        if heads[0][0] != meta["version"]:
            F.error(f, "changelog", f"newest entry is [{heads[0][0]}] but metadata version is {meta['version']}; the release pull request changes both together (newest first)")
    for k in range(1, len(heads)):
        a_, b_ = parse_semver(heads[k][0]), parse_semver(heads[k - 1][0])
        if a_ and b_ and a_ >= b_:
            F.error(f, "changelog", f"entries are not newest-first around [{heads[k][0]}]")
            break
    for v, d in heads:
        try:
            date.fromisoformat(d)
        except ValueError:
            F.error(f, "changelog", f"entry [{v}] has an invalid date {d}")
    if PLACEHOLDER_RE.search(md):
        F.error(f, "placeholder", "template placeholder (__X__) left in file")


def check_version(a: Applet, meta: dict | None):
    """The version is bumped once, in the release pull request, not with each
    change. All we check here is that it never goes below the last release:
    the highest local v<x.y.z> tag (`git fetch --tags` brings them here).
    Silent when the repo has no such tag yet."""
    if not meta or not isinstance(meta.get("version"), str) or not parse_semver(meta["version"]):
        return
    last = latest_release_tag(a.dir)
    if last and parse_semver(meta["version"]) < parse_semver(last):
        F.error(rel(a.metadata_path), "version", f"version {meta['version']} is lower than the last release, v{last}")


LONG_LIST = re.compile(r"""\[\s*(?:(['"`])[^'"`\n]*\1\s*,\s*){6,}(['"`])[^'"`\n]*\2\s*,?\s*\]""")


def pinned_lists(src: str) -> list[tuple[int, int]]:
    """(line, items) for each T.eq(…) that expects a long literal list, unless
    its test is marked with a '// the whole list' comment."""
    out = []
    for m in LONG_LIST.finditer(src):
        before = src[max(0, m.start() - 400):m.start()]
        call = before.rfind("T.eq(")
        if call < 0 or ";" in before[call:] or re.search(r"\bof\s*$", before):
            continue
        head = src[:m.start()]
        test = head[max(head.rfind("\nT.run("), head.rfind("\nT.runAsync("), 0):]
        if re.search(r"//\s*the whole list", test, re.I):
            continue
        out.append((head.count("\n") + 1, len(re.split(r"['\"`]\s*,", m.group(0)))))
    return out


def check_tests(a: Applet):
    """tests/: the suites, their fixtures and harness.json. The pull request
    checks run them; here we only make sure they are there and wired up."""
    t = a.tests_dir
    tr = rel(t)
    if not t.is_dir():
        F.error(tr, "tests", "missing tests/ folder (the test suites, fixtures and harness.json); start from cinnamon-tools/templates/smoke.test.js")
        return
    suites = sorted(list(t.glob("*.test.js")) + list(t.glob("*.test.py")))
    if not suites:
        F.error(tr, "tests", "no test suites (tests/*.test.js or tests/*.test.py)")
    for p in suites:
        if p.suffix == ".js":
            for line, n in pinned_lists(p.read_text(encoding="utf-8", errors="replace")):
                F.warn(f"{rel(p)}:{line}", "pinned-list", f"expects a whole list of {n} entries, so every new entry breaks it; check only the entries this test is about, or mark the one test that pins the list with a \"// the whole list\" comment")
    if not a.spec_path.exists():
        F.error(rel(a.spec_path), "tests", "missing live-check settings (copy cinnamon-tools/templates/harness.json)")
    elif not isinstance(load_json(a.spec_path), dict):
        F.error(rel(a.spec_path), "tests", "not a valid JSON object")


# ---------------------------------------------------------------------------
if ONLY:
    found = find_applet(name_of_folder(ONLY))
    if not found:
        print(f"check.py: no applet repo named {ONLY} in {GROUP_ROOT}")
        sys.exit(2)
    applets = [found]
else:
    check_group()
    applets = discover_applets()
metas = {a.uuid: check_applet(a) for a in applets}

for line in F.render(quiet=QUIET):
    print(line)
if not QUIET or F.errors:
    print(f"{len(applets)} applet(s) checked, {F.errors} error(s), {F.warnings} warning(s)")
sys.exit(1 if F.errors else 0)
