# Reference

## Settings

`/etc/glance-bookmarks-editor/env` (root, 600) holds `KEY=value` lines, read by the app itself, with no quotes or
expansion.

| Key | Default | Meaning |
|---|---|---|
| `GE_DATA` | `/opt/glance_data` | Glance data directory (the folder with `glance.yml`) |
| `GE_LISTEN` / `GE_PORT` | `0.0.0.0` / `5000` | where the editor listens |
| `GE_TRUSTED_PROXY` | *(empty)* | reverse proxy IP; `X-Forwarded-For` is trusted only from it |
| `GE_PUBLIC_URL` | *(empty)* | the editor's URL; `--migrate` points a `Glance Bookmarks` tile at it |
| `GE_GLANCE_URL` | *(empty)* | your Glance URL; shows an "Open Glance" link in the header |
| `GE_COOKIE_SECURE` | `1` | `0` allows login over plain HTTP (not recommended) |
| `GE_SESSION_IDLE` | `43200` | session idle timeout in seconds (12 h) |
| `GE_USER` | `admin` | login name |
| `GE_PASSWORD_HASH` | | set by `glance-bookmarks-editor passwd` |
| `GE_GLANCE_BIN` | *(from `systemctl show glance`)* | Glance binary, if detection fails |
| `GE_GLANCE_UNIT` | `glance` | Glance's systemd unit (used for reload checks) |
| `GE_GLANCE_ENV` | `<GE_DATA>/.env` | Glance's secrets file, loaded only into the validation subprocess |


To change a setting: `nano /etc/glance-bookmarks-editor/env`, then `systemctl restart glance-bookmarks-editor`. During the first
install, `install.sh` takes `GE_DATA`, `GE_PORT`, `GE_TRUSTED_PROXY`, `GE_PUBLIC_URL` and `GE_GLANCE_URL` from
the environment and writes them to this file. Later updates keep the file as it is.

## Command line

| Command | Does |
|---|---|
| `glance-bookmarks-editor passwd` | set or reset the admin password (hidden prompt); signs out every session |
| `glance-bookmarks-editor validate` | run `glance config:validate` on the live config, with `.env` loaded |
| `glance-bookmarks-editor migrate --plan <dir>` | write the migration diff to `<dir>`; changes nothing |
| `glance-bookmarks-editor version` | print the installed version |

| Installer | Does |
|---|---|
| `install.sh --plan` | show what `--migrate` would change; changes nothing |
| `install.sh --migrate` | first install: back up the config, move the tile widgets into `local/links.yml`, install and start the editor |
| `install.sh` | install or update; keeps `/etc/glance-bookmarks-editor/env` and `local/` |

`--migrate` in detail:

- checks that the live config validates (it stops if it doesn't)
- backs up `glance.yml`, `pages/`, `widgets/`, `local/` and `assets/` to `/root/glance-pre-editor-<date>.tar.gz`
- moves the bookmarks widgets into `local/links.yml` and puts the include line in `pages/home.yml` (see
  [Config layout](config-layout.md#the-rules-the-editor-relies-on) for which ones move). Before writing, it
  checks that Glance expands the old and new config to exactly the same pages. If anything fails, it restores
  the backup.
- if `GE_PUBLIC_URL` is set and a tile is titled `Glance Bookmarks`, points that tile at `GE_PUBLIC_URL`
- it's safe to run again: once the include line exists, the move is skipped

## How it works

| Piece | Where |
|---|---|
| App (Python 3 standard library + `python3-yaml`, vanilla JS, vendored SortableJS) | `/opt/glance-bookmarks-editor/` |
| Settings and password hash (root, 600) | `/etc/glance-bookmarks-editor/env` |
| Service | `glance-bookmarks-editor.service`, port 5000 |
| Command | `/usr/local/sbin/glance-bookmarks-editor` |
| Log | `journalctl -u glance-bookmarks-editor` and `/var/log/glance-bookmarks-editor/glance-bookmarks-editor.log` |
| selfh.st index cache (24 h) | `/var/cache/glance-bookmarks-editor/selfhst-index.json` |

**Files it writes.** It writes only these, and nothing else:

- `<data dir>/local/links.yml`: both tile widgets
- `<data dir>/local/countdown.yml`: the countdown dates
- `<data dir>/local/.history/`: the last 30 versions of each of those
- `<data dir>/assets/icons/*.webp`: icons downloaded from selfh.st

**Save pipeline.** Every save runs these steps:

1. Builds the file with a stable key order and 2-space indents.
2. Copies `glance.yml`, `pages/`, `widgets/`, `local/` and `assets/custom.css` to a temp dir, with the new file
   swapped in.
3. Runs `glance config:validate` on the copy, with `.env` loaded into that subprocess only. Secret values are
   masked in any message.
4. If the check fails, nothing changes and you see Glance's error.
5. If it passes, the old file goes into History and the new file replaces the live one atomically.
6. It watches `journalctl -u glance` for 3 s and reports "Glance reloaded", or Glance's `Config has errors` line.

**Icons.** Search uses selfh.st's `index.json`. The server downloads only from
`https://raw.githubusercontent.com/selfhst/icons/main/webp/<name>.webp`, and only when:

- the name matches `^[a-z0-9-]+$`
- the file is at most 512 KB and arrives within 10 s, with no redirects
- the file starts with the RIFF/WEBP magic bytes

There are no user-supplied URLs and no uploads. A file can't be deleted while a link uses it, or while
`pages/`, `widgets/`, `glance.yml` or `custom.css` mentions it.

## Security

- Single admin user. The password is hashed with PBKDF2-SHA256 at 600,000 iterations and set at a hidden prompt.
- The session cookie is `HttpOnly; Secure; SameSite=Strict`, with a 12 h idle timeout. Changing the password
  signs out every session.
- Every POST needs a CSRF token, and cross-origin POSTs are refused.
- Login allows 5 failed tries per 5 min per IP.
- Headers: CSP `default-src 'self'; img-src 'self' https://cdn.jsdelivr.net https://raw.githubusercontent.com`,
  and `X-Frame-Options: DENY`.
- **It runs as root.** It needs Glance's root-only `.env` to validate configs that use `${VAR}`s, and it writes
  into the Glance data directory. Treat it as an admin interface: reachable only through your HTTPS proxy, from
  your own devices, behind its login.
