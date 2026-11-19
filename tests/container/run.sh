#!/bin/bash
# Full install test in a stand-in Glance host (Debian 13 + systemd + Glance 0.8.7).
#   tests/container/run.sh [glance_data_dir]   (default: tests/fixture/glance_data)
# Output (logs, screenshots, migration diff) goes to tests/out/.
set -euo pipefail
cd "$(dirname "$0")/../.."
DATA_SRC=${1:-tests/fixture/glance_data}
OUT=tests/out
NAME=ge-test
PW='test-password-123456'
rm -rf "$OUT"; mkdir -p "$OUT"
tests/get-glance.sh >/dev/null
TARBALL=$(./build-release.sh | sed -n 1p)
docker build -q -t glance-bookmarks-editor-test tests/container >/dev/null
docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock -p 127.0.0.1:15000:5000 -p 127.0.0.1:18080:8080 glance-bookmarks-editor-test >/dev/null
x() { docker exec $NAME bash -c "$*"; }
step() { printf '\n######## %s\n' "$*"; }
for _ in $(seq 1 30); do x 'systemctl is-system-running 2>/dev/null' | grep -qE 'running|degraded' && break; sleep 1; done

step "Load the Glance config copy into /opt/glance_data"
x 'mkdir -p /opt/glance_data'
docker cp "$DATA_SRC/." $NAME:/opt/glance_data/
if [ "$DATA_SRC" = tests/fixture/glance_data ]; then
  x 'printf "EXAMPLE_API_KEY=s3cret-Example-value-42\n" > /opt/glance_data/.env'
fi
x 'chown -R root:root /opt/glance_data && chmod 600 /opt/glance_data/.env && systemctl restart glance && sleep 2 && systemctl is-active glance && df -h / | tail -1'
for _ in $(seq 1 20); do curl -fs -o /dev/null http://127.0.0.1:18080/ && break; sleep 0.5; done
node tests/ui/ui-test.mjs home http://127.0.0.1:18080/ "$OUT/home-before"

docker cp "$TARBALL" $NAME:/root/
F=$(basename "$TARBALL")
x "cd /root && tar xzf $F"

step "install.sh --plan (changes nothing)"
x 'sha256sum /opt/glance_data/pages/home.yml' > "$OUT/home.sha.before"
x '/root/glance-bookmarks-editor/install.sh --plan' 2>&1 | tee "$OUT/plan.log"
x 'sha256sum /opt/glance_data/pages/home.yml' | diff - "$OUT/home.sha.before" && echo "ok: --plan left home.yml unchanged"

step "install.sh --migrate (no terminal)"
x 'GE_PUBLIC_URL=https://glance-bookmarks.example.com/ GE_GLANCE_URL=http://localhost:18080/ /root/glance-bookmarks-editor/install.sh --migrate' 2>&1 | tee "$OUT/migrate.log"
x 'systemctl is-active glance glance-bookmarks-editor; ls -la /opt/glance-bookmarks-editor | head -5; ls /root/'
x 'glance-bookmarks-editor version; printf "%s\n" "'$PW'" | glance-bookmarks-editor passwd --stdin'
x 'stat -c "%a %U %n" /etc/glance-bookmarks-editor/env /opt/glance_data/local/links.yml /opt/glance_data/pages/home.yml'
x 'grep -c "^GE_PASSWORD_HASH=pbkdf2_sha256:600000:" /etc/glance-bookmarks-editor/env'

step "Re-run --migrate and plain install (idempotent, keeps settings)"
x 'sha256sum /etc/glance-bookmarks-editor/env /opt/glance_data/local/links.yml /opt/glance_data/pages/home.yml' > "$OUT/sums1"
x '/root/glance-bookmarks-editor/install.sh --migrate' > "$OUT/migrate2.log" 2>&1 && grep -E 'already|nothing to change|Done' "$OUT/migrate2.log"
x '/root/glance-bookmarks-editor/install.sh' > "$OUT/update.log" 2>&1 && grep -E 'kept|Done' "$OUT/update.log"
x 'sha256sum /etc/glance-bookmarks-editor/env /opt/glance_data/local/links.yml /opt/glance_data/pages/home.yml' | diff - "$OUT/sums1" && echo "ok: settings, links.yml and home.yml unchanged by re-runs"

step "Glance after migration"
sleep 1
node tests/ui/ui-test.mjs home http://127.0.0.1:18080/ "$OUT/home-after"
diff "$OUT/home-before.tiles.txt" "$OUT/home-after.tiles.txt" && echo "tiles identical" || echo "(tile differences above — expected: only the Glance Bookmarks URL)"
curl -s http://127.0.0.1:15000/health; echo
x 'journalctl -u glance --no-pager -n 50 -o cat | grep -E "Config|Starting" | tail -5'

step "Editor UI test (real Glance reload via journald)"
node tests/ui/ui-test.mjs editor http://localhost:15000/ "$OUT/ui" "$PW"
x 'journalctl -u glance --no-pager -o cat | grep -c "Config file changed"' | sed 's/^/glance reloads logged: /'
x 'journalctl -u glance --no-pager -o cat | grep -c "Config has errors"' | sed 's/^/glance config errors logged: /' || true
x 'ls /opt/glance_data/local/.history | head; tail -n 5 /var/log/glance-bookmarks-editor/glance-bookmarks-editor.log'
x 'grep -rl "s3cret-Example" /var/log/glance-bookmarks-editor /var/cache/glance-bookmarks-editor /opt/glance_data/local 2>/dev/null' && echo "FAIL: secret leaked" || echo "ok: secret not in editor logs, cache or local/"
x 'journalctl -u glance-bookmarks-editor --no-pager -o cat | grep -c s3cret-Example' || echo "ok: secret not in the editor journal"
echo; echo "Done. Output in $OUT/. Container $NAME is still running (docker rm -f $NAME)."
