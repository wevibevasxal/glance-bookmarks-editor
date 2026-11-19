#!/bin/bash
# Build dist/glance-bookmarks-editor-<version>.tar.gz (app, install.sh, unit, docs, starter config). No secrets, no test files.
set -euo pipefail
cd "$(dirname "$0")"
VER=$(cat app/VERSION)
mkdir -p dist
OUT=dist/glance-bookmarks-editor-$VER.tar.gz
python3 -I - "$OUT" <<'PY'
import os, sys, tarfile, time
out = sys.argv[1]
files = ["install.sh", "README.md", "LICENSE", "config/glance-bookmarks-editor.service"]
for root, dirs, names in (w for top in ("app", "docs", "examples") for w in os.walk(top)):
    dirs[:] = sorted(d for d in dirs if d != "__pycache__")
    files += [os.path.join(root, n) for n in sorted(names) if not n.endswith(".pyc") and n != ".DS_Store"]
mtime = int(time.time())
def info(path):
    ti = tarfile.TarInfo("glance-bookmarks-editor/" + path)
    st = os.stat(path)
    ti.size, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = st.st_size, mtime, 0, 0, "root", "root"
    ti.mode = 0o755 if path in ("install.sh", "app/main.py") else 0o644
    return ti
with tarfile.open(out, "w:gz") as t:
    dirs = set()
    for f in files:                      # every ancestor directory, so each gets an entry
        d = os.path.dirname(f)
        while d:
            dirs.add(d)
            d = os.path.dirname(d)
    dirs = sorted(dirs)
    for d in ["."] + dirs:
        ti = tarfile.TarInfo("glance-bookmarks-editor" + ("" if d == "." else "/" + d)); ti.type = tarfile.DIRTYPE
        ti.mode, ti.mtime, ti.uname, ti.gname = 0o755, mtime, "root", "root"; t.addfile(ti)
    for f in files:
        with open(f, "rb") as fh:
            t.addfile(info(f), fh)
print(out)
PY
# the same file under a fixed name, so .../releases/latest/download/glance-bookmarks-editor.tar.gz always works
cp "$OUT" dist/glance-bookmarks-editor.tar.gz
(cd dist && shasum -a 256 "$(basename "$OUT")" glance-bookmarks-editor.tar.gz) | tee "$OUT.sha256"
tar tzvf "$OUT" | awk '{print $1, $NF}'
