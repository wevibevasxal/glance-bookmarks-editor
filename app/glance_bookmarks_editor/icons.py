"""Icon library (assets/icons) and selfh.st downloads.

Downloads only ever come from the fixed selfh.st URL pattern with a name matching ^[a-z0-9-]+$:
no user-supplied URLs, max 512 KB, 10 s timeout, must be a WebP (RIFF....WEBP), no redirects.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

import yaml

from . import model, pipeline, settings

log = logging.getLogger("glance-bookmarks-editor")

NAME = re.compile(r"^[a-z0-9-]{1,100}$")
MAX_BYTES = 512 * 1024
TIMEOUT = 10
INDEX_TTL = 24 * 3600
INDEX_MAX = 8 * 1024 * 1024
_index_lock = threading.Lock()
_index = {"loaded": 0.0, "items": None, "refs": set(), "stale": False}


class IconError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not followed", headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


def _fetch(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "glance-bookmarks-editor"})
    with _opener.open(req, timeout=TIMEOUT) as r:
        n = r.headers.get("Content-Length")
        if n and n.isdigit() and int(n) > limit:
            raise IconError(f"file is too large ({int(n) // 1024} KB, max {limit // 1024} KB)")
        data = r.read(limit + 1)
        if len(data) > limit:
            raise IconError(f"file is too large (max {limit // 1024} KB)")
        return data


# ---------------------------------------------------------------- selfh.st index

def _cache_path():
    return os.path.join(settings.get("GE_CACHE_DIR"), "selfhst-index.json")


def _compact(raw):
    items = []
    for x in raw:
        ref = x.get("Reference") if isinstance(x, dict) else None
        if not isinstance(ref, str) or not NAME.match(ref) or x.get("WebP") != "Yes":
            continue
        items.append([ref, str(x.get("Name") or ref)[:80], x.get("Light") == "Yes"])
    return items


def index(force=False):
    """[[reference, display name, has_light], ...] from selfh.st index.json, cached 24 h on disk."""
    with _index_lock:
        now = time.time()
        if _index["items"] is not None and not force and now - _index["loaded"] < INDEX_TTL:
            return _index["items"], _index["stale"]
        path = _cache_path()
        raw, stale = None, False
        try:
            if not force and os.path.isfile(path) and now - os.path.getmtime(path) < INDEX_TTL:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
        except (OSError, ValueError):
            raw = None
        if raw is None:
            try:
                data = _fetch(settings.get("GE_INDEX_URL"), INDEX_MAX)
                raw = json.loads(data.decode("utf-8"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                pipeline._atomic_write(path, data, mode=0o644)
            except (OSError, ValueError, IconError, urllib.error.URLError) as e:
                log.warning("selfh.st index fetch failed: %s", e)
                try:
                    with open(path, encoding="utf-8") as f:
                        raw, stale = json.load(f), True
                except (OSError, ValueError):
                    raise IconError("couldn't load the selfh.st icon list (no internet?)")
        if not isinstance(raw, list):
            raise IconError("the selfh.st icon list has an unexpected format")
        items = _compact(raw)
        _index.update(loaded=now, items=items, refs={i[0] for i in items}
                      | {i[0] + "-light" for i in items if i[2]}, stale=stale)
        return items, stale


def known_refs():
    try:
        index()
    except IconError:
        pass
    return _index["refs"]


# ---------------------------------------------------------------- download

def is_webp(data):
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def download(name, force=False):
    """Download selfh.st webp/<name>.webp into assets/icons/<name>.webp."""
    if not isinstance(name, str) or not NAME.match(name):
        raise IconError("icon name must be lower-case letters, digits and dashes")
    dest = os.path.join(model.icons_dir(), f"{name}.webp")
    if os.path.lexists(dest) and not force:
        return {"status": "exists", "file": f"{name}.webp", "icon": model.ICON_PREFIX + f"{name}.webp"}
    url = settings.get("GE_ICON_BASE_URL") + f"{name}.webp"
    try:
        data = _fetch(url, MAX_BYTES)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise IconError(f"selfh.st has no icon called {name!r}")
        raise IconError(f"download failed (HTTP {e.code})")
    except (urllib.error.URLError, OSError) as e:
        raise IconError(f"download failed: {getattr(e, 'reason', e)}")
    if not is_webp(data):
        raise IconError("the downloaded file isn't a WebP image; not saved")
    os.makedirs(model.icons_dir(), exist_ok=True)
    if os.path.islink(dest):
        raise IconError("refusing to replace a symlink")
    pipeline._atomic_write(dest, data, mode=0o644)
    log.info("icon %s.webp downloaded (%d bytes)%s", name, len(data), " (re-download)" if force else "")
    return {"status": "downloaded", "file": f"{name}.webp", "icon": model.ICON_PREFIX + f"{name}.webp",
            "size": len(data)}


# ---------------------------------------------------------------- library

def _other_refs():
    """Files outside local/links.yml that mention /assets/icons/<file> (pages, widgets, glance.yml, css)."""
    root = settings.data_dir()
    paths = [os.path.join(root, "glance.yml"), os.path.join(root, "assets", "custom.css")]
    for d in ("pages", "widgets"):
        try:
            paths += [os.path.join(root, d, n) for n in sorted(os.listdir(os.path.join(root, d)))
                      if n.endswith((".yml", ".yaml"))]
        except FileNotFoundError:
            pass
    refs = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for m in re.finditer(r"/assets/icons/([A-Za-z0-9._-]+)", text):
            refs.setdefault(m.group(1), set()).add(os.path.relpath(p, root))
    return refs


def usage():
    """{file: [link titles]} from local/links.yml."""
    out = {}
    data, _ = pipeline.read(model.LINKS)
    if data is None:
        return out
    try:
        widgets = yaml.safe_load(data.decode("utf-8")) or []
    except yaml.YAMLError:
        return out
    for w in widgets if isinstance(widgets, list) else []:
        for g in (w.get("groups") or []) if isinstance(w, dict) else []:
            for link in (g.get("links") or []) if isinstance(g, dict) else []:
                if isinstance(link, dict):
                    f = model.icon_file(link.get("icon"))
                    if f:
                        out.setdefault(f, []).append(f"{w.get('title') or 'Bookmarks'} › {g.get('title') or '(untitled)'} › "
                                                     f"{link.get('title')}")
    return out


def library():
    d = model.icons_dir()
    used, other = usage(), _other_refs()
    refs = known_refs()
    items = []
    try:
        names = sorted(os.listdir(d), key=str.lower)
    except FileNotFoundError:
        names = []
    for n in names:
        p = os.path.join(d, n)
        if not model._ICON_FILE.match(n) or os.path.islink(p) or not os.path.isfile(p):
            continue
        stem, ext = os.path.splitext(n)
        items.append({"file": n, "icon": model.ICON_PREFIX + n, "size": os.path.getsize(p),
                      "used_by": used.get(n, []), "other_refs": sorted(other.get(n, [])),
                      "selfhst": ext == ".webp" and stem in refs})
    return items


def delete(file):
    if not isinstance(file, str) or not model._ICON_FILE.match(file) or ".." in file:
        raise IconError("bad file name")
    p = os.path.join(model.icons_dir(), file)
    if os.path.islink(p) or not os.path.isfile(p):
        raise IconError("no such icon")
    used = usage().get(file)
    if used:
        raise IconError(f"{file} is used by {len(used)} tile(s): {', '.join(used[:5])}")
    other = _other_refs().get(file)
    if other:
        raise IconError(f"{file} is referenced in {', '.join(sorted(other))}")
    os.unlink(p)
    log.info("icon %s deleted", file)
    return {"status": "deleted", "file": file}


def tmp_cleanup():
    """Remove leftover temp files from interrupted writes."""
    d = model.icons_dir()
    try:
        for n in os.listdir(d):
            if n.startswith(".") and ".tmp-" in n:
                os.unlink(os.path.join(d, n))
    except OSError:
        pass

