"""One-time migration: move the Home page's bookmarks widgets out of pages/home.yml into local/links.yml.

The bookmarks widgets that sit together (next to each other in one column) move, whatever they're
called. When there are several such places, the one with the most widgets moves (the first, on a tie);
bookmarks widgets elsewhere on the page stay where they are and are reported.

Text surgery, not a YAML round trip, so home.yml keeps its formatting and the widgets are copied
verbatim. Before anything is written, the result is proven equivalent: `glance config:print`
of the old and new config must parse to the same structure, except the URL of a tile titled "Glance Bookmarks" (repointed to GE_PUBLIC_URL when that is set).
"""

import copy
import difflib
import os
import re
import shutil
import subprocess
import tempfile

import yaml

from . import model, pipeline, settings

HOME = "pages/home.yml"
INCLUDE = "$include: ../local/links.yml"
_INCLUDE_RE = re.compile(r"^\s*-\s*\$include:\s*\.\./local/links\.yml\s*$")
_BOOKMARKS_RE = re.compile(r"^( *)-\s+type:\s*[\"']?bookmarks[\"']?\s*(#.*)?$")
TILE_TITLE = "Glance Bookmarks"
POINTER = ("# The bookmarks widgets live in local/links.yml (glance-bookmarks-editor). "
           "Keep this include line when you edit this file.")


class MigrationError(Exception):
    pass


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def find_blocks(lines):
    """[(start, end, indent)] for each `- type: bookmarks` list item (end exclusive, trailing blanks dropped)."""
    blocks = []
    for i, line in enumerate(lines):
        m = _BOOKMARKS_RE.match(line.rstrip("\n"))
        if not m:
            continue
        ind = len(m.group(1))
        j = i + 1
        while j < len(lines):
            s = lines[j]
            if s.strip() and _indent(s) <= ind:
                break
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        blocks.append((i, j, ind))
    return blocks


def find_runs(lines):
    """Group the bookmarks blocks into runs: same indent, with only blank or comment lines between."""
    runs = []
    for b in find_blocks(lines):
        if runs:
            prev = runs[-1][-1]
            gap = lines[prev[1]:b[0]]
            if prev[2] == b[2] and all(not x.strip() or x.lstrip().startswith("#") for x in gap):
                runs[-1].append(b)
                continue
        runs.append([b])
    return runs


def _set_tile_url(links_text, url):
    """Change the url of the link titled 'Glance Bookmarks'. Returns (text, old_url or None).
    Does nothing when no URL is configured (GE_PUBLIC_URL empty)."""
    if not url:
        return links_text, None
    lines = links_text.splitlines(True)
    title_re = re.compile(r"^( *)-\s+title:\s*[\"']?" + re.escape(TILE_TITLE) + r"[\"']?\s*$")
    for i, line in enumerate(lines):
        m = title_re.match(line.rstrip("\n"))
        if not m:
            continue
        ind = len(m.group(1))
        for j in range(i + 1, len(lines)):
            s = lines[j]
            if s.strip() and _indent(s) <= ind:
                break
            um = re.match(r"^( +)url:\s*(\S.*?)\s*$", s.rstrip("\n"))
            if um and _indent(s) == ind + 2:
                old = um.group(2).strip("\"'")
                if old == url:
                    return links_text, None
                lines[j] = f"{um.group(1)}url: {url}\n"
                return "".join(lines), old
    return links_text, None


def _config_print(replacements):
    """Parsed `glance config:print` of a temp copy of the config with files swapped in."""
    gbin = settings.glance_bin()
    env = pipeline.load_glance_env()
    proc_env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", **env}
    tmp = tempfile.mkdtemp(prefix="glance-bookmarks-editor-migrate-")
    try:
        pipeline._copy_config(tmp)
        for rel, data in replacements.items():
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        r = subprocess.run([gbin, "--config", os.path.join(tmp, "glance.yml"), "config:print"],
                           env=proc_env, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise MigrationError("glance config:print failed: " + pipeline.redact(r.stderr + r.stdout, env, tmp))
        return yaml.safe_load(r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _find_tile(cfg):
    """Every link dict titled 'Glance Bookmarks' in the parsed config."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("title") == TILE_TITLE and "url" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(cfg)
    return found


def plan(public_url=None):
    """Work out the migration without writing anything. Returns a dict (see keys below)."""
    public_url = public_url or settings.get("GE_PUBLIC_URL")
    root = settings.data_dir()
    with open(os.path.join(root, HOME), encoding="utf-8") as f:
        home = f.read()
    links_path = os.path.join(root, model.LINKS)
    if any(_INCLUDE_RE.match(line) for line in home.splitlines()):
        res = {"already": True, "home_old": home, "home_new": home}
        if os.path.isfile(links_path):
            with open(links_path, encoding="utf-8") as f:
                cur = f.read()
            new, old_url = _set_tile_url(cur, public_url)
            res.update(links_old=cur, links_new=new, tile_old_url=old_url)
        else:
            raise MigrationError("home.yml includes ../local/links.yml but that file is missing — "
                                 "restore it from local/.history or the pre-editor tarball")
        return res
    if "\t" in home:
        raise MigrationError("pages/home.yml contains tab characters; fix them first")
    lines = home.splitlines(True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    runs = find_runs(lines)
    if not runs:
        raise MigrationError(f"no `- type: bookmarks` widget found in {HOME}. Add one where the tiles should "
                             "go (see examples/glance_data/pages/home.yml), then run this again")
    run = max(runs, key=len)           # the biggest run; the first one when several are equal
    left = sum(len(r) for r in runs) - len(run)
    s1, ind = run[0][0], run[0][2]
    e_last = run[-1][1]
    # comment banners directly above the first widget (same indent, no blank line between) move with it
    while s1 > 0 and lines[s1 - 1].lstrip().startswith("#") and _indent(lines[s1 - 1]) == ind:
        s1 -= 1
    body = []
    for k, (bs, be, _) in enumerate(run):
        if k:   # comments between widgets move too; blank lines between them are dropped
            body += [x for x in lines[run[k - 1][1]:bs] if x.strip()]
        body += lines[s1 if k == 0 else bs:be]
    out = []
    for ln in body:
        if not ln.strip():
            out.append("\n")
        elif _indent(ln) < ind:
            raise MigrationError("unexpected indentation inside the bookmarks widgets")
        else:
            out.append(ln[ind:])
    body = out
    links_text = model.LINKS_HEADER + "".join(body)
    links_text, tile_old = _set_tile_url(links_text, public_url)
    new_lines = lines[:s1] + [" " * ind + POINTER + "\n", " " * ind + "- " + INCLUDE + "\n"] + lines[e_last:]
    home_new = "".join(new_lines)

    # Prove it: the parsed links.yml must be exactly the moved bookmarks widgets ...
    widgets = yaml.safe_load(links_text)
    if not (isinstance(widgets, list) and len(widgets) == len(run)
            and all(isinstance(w, dict) and w.get("type") == "bookmarks" for w in widgets)):
        raise MigrationError("couldn't cut the bookmarks widgets out cleanly; not migrating automatically")
    # ... and the whole expanded config must be unchanged apart from the tile URL.
    before = _config_print({})
    after = _config_print({HOME: home_new.encode(), model.LINKS: links_text.encode()})
    expected = copy.deepcopy(before)
    if tile_old is not None:
        tiles = _find_tile(expected)
        if len(tiles) != 1:
            raise MigrationError(f"expected one {TILE_TITLE!r} tile, found {len(tiles)}")
        tiles[0]["url"] = public_url
    if after != expected:
        raise MigrationError("the migrated config doesn't expand to the same pages; not migrating")
    return {"already": False, "home_old": home, "home_new": home_new, "links_old": None,
            "links_new": links_text, "tile_old_url": tile_old, "tile_found": bool(_find_tile(before)),
            "moved": [w.get("title") or "(untitled)" for w in widgets], "left": left}


def diff(a, b, name_a, name_b):
    return "".join(difflib.unified_diff((a or "").splitlines(True), (b or "").splitlines(True), name_a, name_b))


def apply(p):
    """Write the planned files after validating them on a temp copy. Returns a list of actions."""
    root = settings.data_dir()
    actions = []
    repl = {}
    if p["home_new"] != p["home_old"]:
        repl[HOME] = p["home_new"].encode()
    if p.get("links_new") is not None and p["links_new"] != p.get("links_old"):
        repl[model.LINKS] = p["links_new"].encode()
    if not repl:
        return ["nothing to change (already migrated, tile already updated)"]
    ok, msg = pipeline.validate_config(repl)
    if not ok:
        raise MigrationError("the migrated config doesn't validate: " + msg)
    if model.LINKS in repl:
        lp = os.path.join(root, model.LINKS)
        if os.path.isfile(lp):
            with open(lp, "rb") as f:
                pipeline.add_history(model.LINKS, f.read())
        like = os.stat(os.path.join(root, model.COUNTDOWN)) if os.path.isfile(os.path.join(root, model.COUNTDOWN)) \
            else None
        pipeline._atomic_write(lp, repl[model.LINKS], mode=0o644, like=like)
        actions.append(f"wrote {model.LINKS}")
    if HOME in repl:
        hp = os.path.join(root, HOME)
        pipeline._atomic_write(hp, repl[HOME], like=os.stat(hp))
        n = len(p.get("moved") or [])
        actions.append(f"replaced {n} bookmarks widget{'s' if n != 1 else ''} in {HOME} with `- {INCLUDE}`")
    ok, msg = pipeline.validate_config(live=True)
    if not ok:
        raise MigrationError("the live config doesn't validate after the migration: " + msg)
    actions.append("live config validates")
    return actions
