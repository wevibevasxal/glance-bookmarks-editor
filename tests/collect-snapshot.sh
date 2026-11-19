#!/bin/bash
# Read-only snapshot of a Proxmox LXC that runs Glance, for testing the editor against a copy of a real
# config (tests/container/run.sh <unpacked glance_data>). Run on the Proxmox host as root:
#
#   ./collect-snapshot.sh <ctid> [glance_data_dir]      (default dir: /opt/glance_data)
#
# Writes only /root/ge-collect/ and /root/ge-collect.tar.gz on the host; nothing is written inside the
# container. The Glance .env is NOT copied (only its key names are listed).
set -uo pipefail
CT=${1:?usage: $0 <ctid> [glance_data_dir]}
GD=${2:-/opt/glance_data}
D=/root/ge-collect
rm -rf "$D" && mkdir -p "$D"

pct exec "$CT" -- tar czf - -C "$GD" --exclude=.env --exclude='*.bak*' --exclude=.history \
  glance.yml pages widgets local assets > "$D/glance_data.tar.gz"

pct exec "$CT" -- env GD="$GD" bash -s > "$D/info.txt" 2>&1 <<'INNER'
echo "## os";      cat /etc/os-release | head -2; uname -r
echo "## df";      df -h /
echo "## glance";  systemctl show -p ExecStart --value glance; systemctl cat glance
echo "## existing glance-bookmarks-editor unit"; systemctl cat glance-bookmarks-editor
echo "state: $(systemctl is-active glance-bookmarks-editor) / $(systemctl is-enabled glance-bookmarks-editor)"
ss -ltnp | grep -E ':(5000|8080) '
echo "## existing glance-bookmarks-editor files"
for p in $(systemctl show -p ExecStart --value glance-bookmarks-editor | grep -o '/[^ ;]*') \
         $(systemctl show -p WorkingDirectory --value glance-bookmarks-editor); do
  echo "-- $p"; ls -la "$p" 2>&1 | head -40
  [ -d "$p" ] && { du -sh "$p"; find "$p" -maxdepth 2 | head -60; }
done
echo "## python"
command -v python3 && python3 --version
python3 -c 'import yaml; print("python3-yaml", yaml.__version__)' 2>&1
echo "## .env key names (values not read)"; cut -d= -f1 "$GD/.env" 2>/dev/null
echo "## internet from the container"
for u in https://raw.githubusercontent.com/selfhst/icons/main/index.json \
         https://raw.githubusercontent.com/selfhst/icons/main/webp/immich.webp \
         https://cdn.jsdelivr.net/gh/selfhst/icons/webp/immich.webp; do
  if command -v curl >/dev/null; then curl -s -o /dev/null -w "%{http_code} $u\n" --max-time 10 "$u" || echo "fail $u"
  else python3 -c "import sys,urllib.request as r; print(r.urlopen(sys.argv[1], timeout=10).status, sys.argv[1])" "$u" 2>&1 | tail -1; fi
done
INNER

{ echo "## pct config $CT"; pct config "$CT"
  echo "## /etc/pve/firewall/$CT.fw"; cat "/etc/pve/firewall/$CT.fw" 2>&1; } > "$D/pct.txt"

tar czf /root/ge-collect.tar.gz -C /root ge-collect && ls -l /root/ge-collect.tar.gz && sha256sum /root/ge-collect.tar.gz
