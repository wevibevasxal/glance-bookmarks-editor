"""Settings: defaults, overlaid by /etc/glance-bookmarks-editor/env (KEY=VALUE), overlaid by GE_* process env.

The settings file is read by the app itself (not by systemd), so values are taken literally:
no quoting, no variable expansion.
"""

import os
import re
import shutil
import subprocess
import threading

SETTINGS_FILE = os.environ.get("GE_SETTINGS", "/etc/glance-bookmarks-editor/env")

DEFAULTS = {
    "GE_LISTEN": "0.0.0.0",
    "GE_PORT": "5000",
    "GE_DATA": "/opt/glance_data",
    "GE_GLANCE_UNIT": "glance",
    "GE_GLANCE_BIN": "",            # empty = detect from `systemctl show -p ExecStart glance`
    "GE_GLANCE_ENV": "",            # empty = <GE_DATA>/.env
    "GE_USER": "admin",
    "GE_PASSWORD_HASH": "",
    "GE_TRUSTED_PROXY": "",             # reverse proxy IP; X-Forwarded-For is trusted only from here
    "GE_SESSION_IDLE": "43200",         # 12 h
    "GE_COOKIE_SECURE": "1",            # 0 = allow login over plain HTTP (not recommended)
    "GE_CACHE_DIR": "/var/cache/glance-bookmarks-editor",
    "GE_LOG_DIR": "/var/log/glance-bookmarks-editor",
    "GE_PUBLIC_URL": "",                # the editor's own URL (optional; used for a "Glance Bookmarks" tile)
    "GE_GLANCE_URL": "",                # your Glance dashboard URL (optional; "Open Glance" link in the header)
    # Fixed selfh.st locations. Only overridden by the test suite (local mock server).
    "GE_ICON_BASE_URL": "https://raw.githubusercontent.com/selfhst/icons/main/webp/",
    "GE_INDEX_URL": "https://raw.githubusercontent.com/selfhst/icons/main/index.json",
}

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_lock = threading.Lock()
_cache = {"mtime": None, "values": {}}


def parse_env_text(text):
    """Parse KEY=VALUE lines. Blank lines and # comments are skipped. Values are literal."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        if not _KEY.match(k):
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def _file_values():
    try:
        st = os.stat(SETTINGS_FILE)
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] != (st.st_mtime_ns, st.st_size):
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    _cache["values"] = parse_env_text(f.read())
            except OSError:
                _cache["values"] = {}
            _cache["mtime"] = (st.st_mtime_ns, st.st_size)
        return dict(_cache["values"])


def get(key):
    """Current value of a setting (re-reads the settings file when it changed)."""
    if key in os.environ and key.startswith("GE_") and key != "GE_PASSWORD_HASH":
        return os.environ[key]
    vals = _file_values()
    return vals.get(key, DEFAULTS.get(key, ""))


def data_dir():
    return os.path.realpath(get("GE_DATA"))


def glance_env_file():
    return get("GE_GLANCE_ENV") or os.path.join(data_dir(), ".env")


def glance_bin():
    """Path of the glance binary: setting, else systemd ExecStart of the glance unit, else PATH."""
    b = get("GE_GLANCE_BIN")
    if b:
        return b
    unit = get("GE_GLANCE_UNIT")
    if shutil.which("systemctl"):
        try:
            out = subprocess.run(["systemctl", "show", "-p", "ExecStart", "--value", unit],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"path=(\S+)", out)
            if m and os.path.isfile(m.group(1)):
                return m.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    return shutil.which("glance") or ""


def write_setting(key, value, path=None):
    """Set KEY=value in the settings file atomically (mode 600), keeping other lines."""
    path = path or SETTINGS_FILE
    if not _KEY.match(key) or "\n" in value:
        raise ValueError("bad setting")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    done = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        lines.append(f"{key}={value}")
    d = os.path.dirname(path) or "."
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
