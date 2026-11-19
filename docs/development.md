# Development

```bash
tests/get-glance.sh                           # download Glance release binaries for the tests (gitignored)
python3 -m unittest discover -s tests -v      # unit + HTTP tests against tests/fixture with the real glance binary
(cd tests/ui && npm install)                  # Playwright, for the container test's screenshots and UI test
tests/container/run.sh [glance_data_dir]      # Debian 13 + systemd + Glance container: install, migrate, UI test
./build-release.sh                            # dist/glance-bookmarks-editor-<version>.tar.gz
```

`tests/container/run.sh` can also run against a copy of a real config (`tests/collect-snapshot.sh` collects
one from a Proxmox LXC, without `.env`). Output goes to `tests/out/`.

## Layout

| Path | What |
|---|---|
| `app/main.py` | command line: `serve`, `passwd`, `validate`, `migrate`, `version` |
| `app/glance_bookmarks_editor/` | `server` (WSGI routes), `auth`, `model` (tiles and countdown ⇄ YAML), `pipeline` (validate, save, history), `icons`, `migrate`, `settings` |
| `app/static/` | the single-page UI (vanilla JS; SortableJS and JetBrains Mono vendored) |
| `install.sh` | installer, updater, `--plan`, `--migrate` |
| `config/glance-bookmarks-editor.service` | systemd unit |
| `build-release.sh` | builds `dist/glance-bookmarks-editor-<version>.tar.gz` (app, installer, unit, docs, starter config, README, LICENSE), a copy named `glance-bookmarks-editor.tar.gz`, and the `.sha256` file |
| `examples/glance_data/` | starter config in the layout the editor expects |
| `tests/` | `test_editor.py`, `fixture/`, `container/`, `ui/` |

## Releasing

1. Bump `app/VERSION`, run the unit tests and `tests/container/run.sh`.
2. `./build-release.sh`, then tag `v<version>` and publish a GitHub release with all three files from `dist/`. The
   fixed-name copy is what the docs download: `releases/latest/download/glance-bookmarks-editor.tar.gz`.

Requirements for the tests: Python 3 with PyYAML, Docker (for `tests/container/run.sh`) and Node.js (for the
Playwright UI test).
