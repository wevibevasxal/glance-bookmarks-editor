# CLAUDE.md — glance-bookmarks-editor

A small web editor that runs next to [Glance](https://github.com/glanceapp/glance) on the same host. It edits the
Home page's bookmarks widgets (any number, any titles), the Countdown dates and the local icon library, with no YAML editing.
The user-facing docs are in [README.md](README.md); this file is for working on the code.

**Name:** Glance Bookmarks Editor (`glance-bookmarks-editor`: repo, service, command, `/opt` and `/etc` paths;
UI header "Glance Bookmarks"). Settings use the `GE_` prefix.

Personal deployment notes (hosts, addresses, how a specific install is delivered) belong in `CLAUDE.local.md`,
which is gitignored. Never commit hostnames, IPs, domains or other identifiers of a real setup: use
`example.com` and `192.168.1.x` placeholders.

## Design rules (keep these)

- **Data ownership.** The editor writes only `local/links.yml`, `local/countdown.yml`, `local/.history/` and
  `assets/icons/` inside the Glance data dir (`GE_DATA`, default `/opt/glance_data`). `install.sh --migrate` is
  the only thing that touches `pages/home.yml`, once.
- **Stack.** Python 3 standard library + `python3-yaml` (apt). Vanilla JS/CSS front end, vendored SortableJS and
  JetBrains Mono. No pip, no venv, no Node, no build step, no CDN at runtime except selfh.st icon previews.
- **Save pipeline.** Render with a stable layout → copy the config to a temp dir with the new file swapped in →
  `glance config:validate` with Glance's `.env` loaded into that subprocess only (never logged or returned;
  values are redacted from messages) → keep the old file in History (last 30) → atomic replace → watch
  `journalctl -u glance` for 3 s.
- **Icons.** selfh.st only. The server downloads from the fixed URL pattern
  `https://raw.githubusercontent.com/selfhst/icons/main/webp/<name>.webp`, name `^[a-z0-9-]+$`, ≤ 512 KB,
  10 s timeout, no redirects, RIFF/WEBP magic. No user-supplied URLs, no uploads. Icons in use can't be deleted.
- **Countdown.** Exactly `birthday-1..3` (`"MM-DD|Name"`) and `event-1..5` (`"MM-DD|Name|emoji"` or
  `"YYYY-MM-DD|Name|emoji"`). Names and emoji may not contain `|` or `"`.
- **Security.** Single admin user; PBKDF2-SHA256 ≥ 600k iterations, set with `glance-bookmarks-editor passwd` at a hidden
  prompt (never an argument). Session cookie `HttpOnly; Secure; SameSite=Strict`, 12 h idle. CSRF token on every
  POST, cross-origin POSTs refused. Login limit 5 tries / 5 min / IP; `X-Forwarded-For` trusted only from
  `GE_TRUSTED_PROXY`. CSP `default-src 'self'; img-src 'self' https://cdn.jsdelivr.net https://raw.githubusercontent.com`,
  `X-Frame-Options: DENY`. `GET /health` → `{"status":"ok"}` without auth.
- **Runs as root** because it validates with Glance's root-only `.env` and writes into the data dir. The README
  tells users to expose it only through an HTTPS reverse proxy.

## Layout

| Path | What |
|---|---|
| `app/main.py` | CLI: `serve`, `passwd`, `validate`, `migrate`, `version` |
| `app/glance_bookmarks_editor/` | `server` (WSGI routes), `auth`, `model` (links/countdown ⇄ YAML), `pipeline` (validate/save/history), `icons`, `migrate`, `settings` |
| `app/static/` | the single-page UI |
| `install.sh` | installer / updater / `--plan` / `--migrate` |
| `build-release.sh` | `dist/glance-bookmarks-editor-<version>.tar.gz` |
| `examples/glance_data/` | starter Glance config in the layout the editor expects |
| `docs/` | user docs; `setup-guide.md` assumes a Proxmox VE helper-script Glance (`/opt/glance/glance`, `/opt/glance_data`). Keep its commands and "Good:" outputs true: replay them in a helper-like container when they change |
| `tests/` | `test_editor.py` (unit + HTTP against `tests/fixture`), `container/run.sh` (Debian + systemd install test), `ui/` (Playwright) |

## Workflow

- Run `python3 -m unittest discover -s tests -v` and `tests/container/run.sh` before a release.
- Bump `app/VERSION` for every release.
