"""Save pipeline: history -> validate a temp copy with `glance config:validate` -> atomic replace -> watch the journal.

The editor only ever writes the files in OWNED (inside local/) and icons in assets/icons/.
"""

import datetime
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from . import settings

log = logging.getLogger("glance-bookmarks-editor")

OWNED = ("local/links.yml", "local/countdown.yml")
HISTORY_DIR = "local/.history"
HISTORY_KEEP = 30
# What config:validate needs to see; local/.history is skipped.
COPY_DIRS = ("pages", "widgets", "local")
SAVE_LOCK = threading.RLock()


class PipelineError(Exception):
    """A save that was refused. `kind` is 'invalid', 'conflict' or 'error'."""

    def __init__(self, message, kind="error", detail=""):
        super().__init__(message)
        self.kind = kind
        self.detail = detail


def sha(data):
    return hashlib.sha256(data).hexdigest()


def path_of(rel):
    if rel not in OWNED:
        raise PipelineError(f"{rel} is not a file the editor manages")
    return os.path.join(settings.data_dir(), rel)


def read(rel):
    """(bytes, sha) of an owned file; (None, None) when it doesn't exist."""
    try:
        with open(path_of(rel), "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return None, None
    return data, sha(data)


# ---------------------------------------------------------------- glance .env

def load_glance_env():
    """Glance's .env as a dict. Never logged or returned to a client."""
    try:
        with open(settings.glance_env_file(), encoding="utf-8") as f:
            return settings.parse_env_text(f.read())
    except OSError:
        return {}


def redact(text, env, tmpdir=None):
    if tmpdir:
        text = text.replace(os.path.realpath(tmpdir), settings.data_dir()).replace(tmpdir, settings.data_dir())
    for v in sorted({v for v in env.values() if len(v) >= 4}, key=len, reverse=True):
        text = text.replace(v, "***")
    return text


# ---------------------------------------------------------------- validation

def _copy_config(dst):
    src = settings.data_dir()
    for name in os.listdir(src):
        p = os.path.join(src, name)
        if os.path.isfile(p) and name.endswith((".yml", ".yaml")):
            shutil.copy2(p, os.path.join(dst, name))
    ignore = shutil.ignore_patterns(".history", ".editor-tmp", "*.tmp-*")
    for d in COPY_DIRS:
        p = os.path.join(src, d)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(dst, d), ignore=ignore, symlinks=False)
    css = os.path.join(src, "assets", "custom.css")
    if os.path.isfile(css):
        os.makedirs(os.path.join(dst, "assets"), exist_ok=True)
        shutil.copy2(css, os.path.join(dst, "assets", "custom.css"))


def validate_config(replacements=None, live=False):
    """Run `glance config:validate`. With `replacements` ({rel: bytes}) it runs on a temp copy
    of the config with those files swapped in; with live=True it checks the live config.
    Returns (ok, message). The message has secrets and temp paths removed."""
    gbin = settings.glance_bin()
    if not gbin or not os.path.isfile(gbin):
        return False, "glance binary not found (set GE_GLANCE_BIN)"
    env = load_glance_env()
    proc_env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
    proc_env.update(env)
    tmp = None
    try:
        if live:
            cfg = os.path.join(settings.data_dir(), "glance.yml")
        else:
            tmp = tempfile.mkdtemp(prefix="glance-bookmarks-editor-validate-")
            _copy_config(tmp)
            for rel, data in (replacements or {}).items():
                p = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
            cfg = os.path.join(tmp, "glance.yml")
        try:
            r = subprocess.run([gbin, "--config", cfg, "config:validate"], env=proc_env,
                               cwd=tmp or settings.data_dir(), capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return False, "glance config:validate timed out"
        out = redact((r.stdout + "\n" + r.stderr).strip(), env, tmp)
        if r.returncode == 0:
            return True, out
        return False, (out or f"config:validate exited with {r.returncode}")[:4000]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- history

def history_dir():
    return os.path.join(settings.data_dir(), HISTORY_DIR)


_HIST_NAME = re.compile(r"^(links\.yml|countdown\.yml)\.(\d{8}-\d{6})(?:-(\d+))?$")


def add_history(rel, data):
    """Keep a copy of the current file before it is replaced. Keeps the newest HISTORY_KEEP."""
    if data is None:
        return None
    d = history_dir()
    os.makedirs(d, mode=0o755, exist_ok=True)
    base = os.path.basename(rel)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{base}.{stamp}"
    n = 1
    while os.path.exists(os.path.join(d, name)):
        name = f"{base}.{stamp}-{n}"
        n += 1
    _atomic_write(os.path.join(d, name), data, mode=0o644)
    for old in list_history(rel)[HISTORY_KEEP:]:
        try:
            os.unlink(os.path.join(d, old["name"]))
        except OSError:
            pass
    return name


def list_history(rel):
    base = os.path.basename(rel)
    try:
        names = os.listdir(history_dir())
    except FileNotFoundError:
        return []
    items = []
    for name in names:
        m = _HIST_NAME.match(name)
        if not m or m.group(1) != base:
            continue
        p = os.path.join(history_dir(), name)
        if not os.path.isfile(p) or os.path.islink(p):
            continue
        st = os.stat(p)
        when = datetime.datetime.strptime(m.group(2), "%Y%m%d-%H%M%S")
        items.append({"name": name, "file": rel, "size": st.st_size,
                      "when": when.strftime("%Y-%m-%d %H:%M:%S"), "_key": (m.group(2), int(m.group(3) or 0))})
    items.sort(key=lambda x: x["_key"], reverse=True)
    for it in items:
        del it["_key"]
    return items


def read_history(rel, name):
    m = _HIST_NAME.match(name or "")
    if not m or m.group(1) != os.path.basename(rel):
        raise PipelineError("no such history entry")
    p = os.path.join(history_dir(), name)
    if not os.path.isfile(p) or os.path.islink(p):
        raise PipelineError("no such history entry")
    with open(p, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- writing

def _atomic_write(path, data, mode=0o644, like=None):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.tmp-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if like is not None:
            os.chmod(tmp, like.st_mode & 0o7777)
            try:
                os.chown(tmp, like.st_uid, like.st_gid)
            except PermissionError:
                pass
        else:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def watch_reload(since, seconds=3.0):
    """Watch `journalctl -u glance` after a write. Returns {state, message}:
    state 'reloaded' | 'errors' | 'quiet' (no reload logged in time) | 'unknown' (no journal)."""
    if not shutil.which("journalctl"):
        return {"state": "unknown", "message": "journalctl isn't available here; reload not checked"}
    unit = settings.get("GE_GLANCE_UNIT")
    deadline = time.time() + seconds
    changed = False
    while True:
        try:
            out = subprocess.run(["journalctl", "-u", unit, "--since", f"@{int(since) - 1}", "-o", "short-unix",
                                  "--no-pager", "-q"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError) as e:
            return {"state": "unknown", "message": f"journalctl failed: {e}"}
        for line in out.splitlines():
            ts, _, msg = line.partition(" ")
            try:
                if float(ts) < since - 0.2:
                    continue
            except ValueError:
                continue
            if "Config has errors" in msg:
                return {"state": "errors", "message": redact(msg[msg.index("Config has errors"):], load_glance_env())}
            if "Config file changed" in msg:
                changed = True
            elif changed and "Starting server" in msg:
                return {"state": "reloaded", "message": "Glance reloaded"}
        if time.time() >= deadline:
            if changed:
                return {"state": "quiet", "message": "Glance noticed the change but hasn't confirmed the reload yet"}
            return {"state": "quiet", "message": "Glance didn't log a reload within 3 s"}
        time.sleep(0.25)


def save(rel, new, base_sha):
    """Run the full pipeline for one owned file. `base_sha` is the sha the client loaded
    (None = file must not exist yet). Returns a result dict; raises PipelineError."""
    if not isinstance(new, bytes):
        new = new.encode("utf-8")
    if b"\t" in new:
        raise PipelineError("tabs are not allowed in YAML", "invalid")
    with SAVE_LOCK:
        cur, cur_sha = read(rel)
        if cur_sha != base_sha:
            raise PipelineError("The file changed since you loaded it (another tab or a manual edit). "
                                "Reload to get the latest version.", "conflict")
        if cur == new:
            return {"status": "unchanged", "sha": cur_sha, "reload": None}
        ok, msg = validate_config({rel: new})
        if not ok:
            log.warning("save of %s refused: config:validate failed", rel)
            raise PipelineError("Glance rejected the change; nothing was saved.", "invalid", msg)
        hist = add_history(rel, cur)
        path = path_of(rel)
        try:
            like = os.stat(path)
        except FileNotFoundError:
            like = None
        since = time.time()
        _atomic_write(path, new, mode=0o644, like=like)
        log.info("saved %s (%d bytes, history %s)", rel, len(new), hist)
        reload = watch_reload(since)
        return {"status": "saved", "sha": sha(new), "history": hist, "reload": reload}
