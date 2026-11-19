#!/bin/bash
# glance-bookmarks-editor installer. Run as root on the machine (or container) where Glance runs, from the unpacked release:
#
#   ./glance-bookmarks-editor/install.sh --plan      show what --migrate would do; changes nothing
#   ./glance-bookmarks-editor/install.sh --migrate   first install: back up the config, move the Home page's
#                                          bookmarks widgets into local/links.yml, install the editor
#   ./glance-bookmarks-editor/install.sh             install or update the editor (keeps /etc/glance-bookmarks-editor/env and local/)
#
# Optional environment (used only when /etc/glance-bookmarks-editor/env is first created):
#   GE_DATA=/opt/glance_data  GE_PORT=5000  GE_TRUSTED_PROXY=<reverse proxy IP>
#   GE_PUBLIC_URL=<editor URL>  GE_GLANCE_URL=<Glance URL>
#
# Safe to re-run. Never touches .env; only writes local/links.yml and pages/home.yml during --migrate.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP=/opt/glance-bookmarks-editor
ETC=/etc/glance-bookmarks-editor
SETTINGS=$ETC/env
UNIT=/etc/systemd/system/glance-bookmarks-editor.service
WRAPPER=/usr/local/sbin/glance-bookmarks-editor
if [ -z "${GE_DATA:-}" ] && [ -f "$SETTINGS" ]; then GE_DATA=$(sed -n 's/^GE_DATA=//p' "$SETTINGS" | tail -1); fi
DATA=${GE_DATA:-/opt/glance_data}
APPMARK=.glance-bookmarks-editor
STAMP=$(date +%F-%H%M%S)
MIN_FREE_KB=153600   # 150 MB
MODE=install

case "${1:-}" in
  "") ;;
  --migrate) MODE=migrate ;;
  --plan) MODE=plan ;;
  -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
  *) echo "unknown option: $1 (use --plan, --migrate or nothing)" >&2; exit 2 ;;
esac

say()  { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
command -v systemctl >/dev/null || die "systemd not found"
[ -f "$SRC/app/main.py" ] || die "run install.sh from the unpacked release (app/ missing next to it)"
VERSION=$(cat "$SRC/app/VERSION" 2>/dev/null || echo "?")

# ------------------------------------------------------------------ preflight
say "glance-bookmarks-editor $VERSION — mode: $MODE"
[ -f "$DATA/glance.yml" ] || die "$DATA/glance.yml not found — set GE_DATA to your Glance config directory"
GLANCE_BIN=$(systemctl show -p ExecStart --value glance 2>/dev/null | sed -n 's/.*path=\([^ ;]*\).*/\1/p')
[ -n "$GLANCE_BIN" ] && [ -x "$GLANCE_BIN" ] || die "can't find the glance binary from 'systemctl show glance'"
info "glance binary: $GLANCE_BIN ($("$GLANCE_BIN" --version 2>/dev/null || echo 'version unknown'))"
FREE=$(df -Pk / | awk 'NR==2 {print $4}')
info "free space on /: $((FREE / 1024)) MB"
[ "$FREE" -ge "$MIN_FREE_KB" ] || die "less than $((MIN_FREE_KB / 1024)) MB free on / (df -h /)"

validate_live() {  # glance config:validate with .env (if any) loaded into this subprocess only
  ( set -a; [ -f "$DATA/.env" ] && . "$DATA/.env"; set +a; "$GLANCE_BIN" --config "$DATA/glance.yml" config:validate ) >/dev/null 2>&1
}

# ------------------------------------------------------------------ deps
need_deps() { ! command -v python3 >/dev/null || ! python3 -c 'import yaml' 2>/dev/null; }
install_deps() {
  if need_deps; then
    say "Installing python3 and python3-yaml (apt)"
    apt-get install -y python3 python3-yaml >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y python3 python3-yaml; }
  fi
  python3 -c 'import yaml' || die "python3-yaml didn't install"
}

# ------------------------------------------------------------------ plan
if [ "$MODE" = plan ]; then
  if need_deps; then
    info "python3/python3-yaml missing: --migrate installs them; the diff needs them, so it can't be shown yet."
  else
    say "Migration plan for $DATA/pages/home.yml"
    out=/root/glance-bookmarks-editor-plan-$STAMP
    GE_SETTINGS=$SETTINGS GE_DATA=$DATA GE_GLANCE_BIN=$GLANCE_BIN python3 -I "$SRC/app/main.py" migrate --plan "$out" || die "the migration can't be done automatically (see above)"
    echo "--- $out/home.yml.diff"; cat "$out/home.yml.diff"
    echo "--- $out/links.yml.new (the new local/links.yml)"; cat "$out/links.yml.new"
  fi
  validate_live && info "live config validates today" || info "WARNING: the live config does NOT validate today"
  say "Nothing was changed."
  exit 0
fi

# ------------------------------------------------------------------ install / migrate
install_deps

if [ "$MODE" = migrate ]; then
  say "Checking the live config before touching it"
  validate_live || die "the live Glance config doesn't validate right now — fix that first (nothing changed)"
  info "valid"

  say "Backing up the Glance config"
  TARBALL=/root/glance-pre-editor-$(date +%F-%H%M).tar.gz
  tar czf "$TARBALL" -C "$DATA" glance.yml pages widgets local assets
  info "$TARBALL ($(du -h "$TARBALL" | cut -f1))"
  HAD_LINKS=no; [ -f "$DATA/local/links.yml" ] && HAD_LINKS=yes

  say "Moving the bookmarks widgets to local/links.yml"
  if ! GE_SETTINGS=$SETTINGS GE_DATA=$DATA GE_GLANCE_BIN=$GLANCE_BIN python3 -I "$SRC/app/main.py" migrate --apply; then
    say "Migration failed — restoring $TARBALL"
    tar xzf "$TARBALL" -C "$DATA" glance.yml pages widgets local
    [ "$HAD_LINKS" = no ] && rm -f "$DATA/local/links.yml"
    validate_live && info "restored config validates" || info "WARNING: restored config does not validate"
    die "migration failed; the config was restored from $TARBALL"
  fi
fi

say "Installing the editor to $APP"
if [ -d "$APP" ] && [ ! -f "$APP/$APPMARK" ]; then
  die "$APP exists but wasn't installed by this installer; move it away first"
fi
rm -rf "$APP.new"
mkdir -p "$APP.new"
cp -a "$SRC/app/." "$APP.new/"
touch "$APP.new/$APPMARK"
find "$APP.new" -name '__pycache__' -prune -exec rm -rf {} +
chown -R root:root "$APP.new"
chmod -R go-w "$APP.new"
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"
rm -rf "$APP.old"
info "app $VERSION copied"

install -d -m 700 "$ETC"
if [ ! -f "$SETTINGS" ]; then
  umask 077
  cat > "$SETTINGS" <<EOF
# glance-bookmarks-editor settings (root, 600). Read by the app itself: KEY=value, no quotes needed.
GE_LISTEN=0.0.0.0
GE_PORT=${GE_PORT:-5000}
GE_DATA=$DATA
GE_USER=admin
# IP of your HTTPS reverse proxy. X-Forwarded-For (for the login rate limit) is trusted only from it.
GE_TRUSTED_PROXY=${GE_TRUSTED_PROXY:-}
# The editor's own URL, e.g. https://glance-bookmarks.example.com/ (optional)
GE_PUBLIC_URL=${GE_PUBLIC_URL:-}
# Your Glance dashboard URL, e.g. https://glance.example.com/ (optional; shows an "Open Glance" link)
GE_GLANCE_URL=${GE_GLANCE_URL:-}
# Session cookies are Secure (HTTPS only). Set to 0 only if you must log in over plain HTTP.
GE_COOKIE_SECURE=1
# GE_GLANCE_BIN=   (empty: taken from 'systemctl show glance')
# GE_PASSWORD_HASH is set by: glance-bookmarks-editor passwd
EOF
  umask 022
  info "created $SETTINGS"
else
  info "kept $SETTINGS"
fi
chmod 600 "$SETTINGS"
install -d -m 755 /var/cache/glance-bookmarks-editor
install -d -m 750 /var/log/glance-bookmarks-editor

cat > "$WRAPPER" <<'EOF'
#!/bin/sh
# glance-bookmarks-editor command (passwd, validate, migrate, version). Managed by glance-bookmarks-editor install.sh.
export GE_SETTINGS=/etc/glance-bookmarks-editor/env
exec /usr/bin/python3 -I /opt/glance-bookmarks-editor/main.py "$@"
EOF
chmod 755 "$WRAPPER"
if [ ! -e /usr/sbin/glance-bookmarks-editor ] || [ "$(readlink /usr/sbin/glance-bookmarks-editor)" = "$WRAPPER" ]; then
  ln -sfn "$WRAPPER" /usr/sbin/glance-bookmarks-editor
fi

say "systemd unit"
install -m 644 "$SRC/config/glance-bookmarks-editor.service" "$UNIT"
systemctl daemon-reload
systemctl enable glance-bookmarks-editor >/dev/null 2>&1
systemctl restart glance-bookmarks-editor
info "glance-bookmarks-editor.service enabled and (re)started"

if ! grep -q '^GE_PASSWORD_HASH=.' "$SETTINGS"; then
  say "Set the admin password (user: admin)"
  if [ -t 0 ]; then
    until "$WRAPPER" passwd; do echo "Try again."; done
  else
    info "No terminal here. Set it from a root shell on this host with: glance-bookmarks-editor passwd"
  fi
fi

say "Health check"
PORT=$(sed -n 's/^GE_PORT=//p' "$SETTINGS" | tail -1); PORT=${PORT:-5000}
ok=no
for _ in $(seq 1 20); do
  if python3 -c 'import urllib.request,sys; sys.exit(0 if b"\"ok\"" in urllib.request.urlopen("http://127.0.0.1:'"$PORT"'/health", timeout=2).read() else 1)' 2>/dev/null; then ok=yes; break; fi
  sleep 0.5
done
if [ "$ok" = yes ]; then
  info "http://127.0.0.1:$PORT/health -> {\"status\":\"ok\"}"
else
  journalctl -u glance-bookmarks-editor -n 20 --no-pager || true
  die "health check failed (journalctl -u glance-bookmarks-editor)"
fi
validate_live && info "Glance config validates" || info "WARNING: the Glance config does not validate"
URL=$(sed -n 's/^GE_PUBLIC_URL=//p' "$SETTINGS" | tail -1)
say "Done. Open ${URL:-http://<this-host>:$PORT/ through your HTTPS reverse proxy}"
