#!/bin/bash
# Download the Glance release binaries used by the tests into tests/bin/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")"
V=${GLANCE_VERSION:-v0.8.8}
get() {  # $1 platform, $2 dest
  [ -x "$2" ] && return 0
  d=$(mktemp -d); curl -fsSL -o "$d/g.tgz" "https://github.com/glanceapp/glance/releases/download/$V/glance-$1.tar.gz"
  tar xzf "$d/g.tgz" -C "$d" glance && install -m 755 "$d/glance" "$2" && rm -rf "$d"
}
case "$(uname -s)-$(uname -m)" in
  Darwin-x86_64) get darwin-amd64 bin/glance ;;
  Darwin-arm64) get darwin-arm64 bin/glance ;;
  Linux-x86_64) get linux-amd64 bin/glance ;;
esac
get linux-amd64 container/glance
bin/glance --version
