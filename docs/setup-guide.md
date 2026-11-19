# Setup guide: from a Proxmox VE helper-script Glance

This guide takes a Glance container created by the
[Proxmox VE community script](https://community-scripts.github.io/ProxmoxVE/scripts?id=glance) and ends with
the editor running behind HTTPS. It takes about 15 minutes.

Installed Glance some other way? The steps are the same. Only the paths differ: find yours with step 2, and
use your folder wherever this guide says `/opt/glance_data`.

**Contents**

1. [Open a shell in the container](#1-open-a-shell-in-the-container)
2. [Check the Glance install](#2-check-the-glance-install)
3. [Download the editor](#3-download-the-editor)
4. [Back up your config](#4-back-up-your-config)
5. [Lay out the config](#5-lay-out-the-config) (fresh install, or [your own config](#5b-you-already-changed-glanceyml))
6. [Check the config and restart Glance](#6-check-the-config-and-restart-glance)
7. [Install the editor](#7-install-the-editor)
8. [Put it behind HTTPS](#8-put-it-behind-https)
9. [Allow port 5000 only from the proxy](#9-allow-port-5000-only-from-the-proxy)
10. [Sign in and test](#10-sign-in-and-test)

## Before you start

- **The container runs Debian** (the helper script's default). The script also offers Alpine, but Alpine has no
  systemd, which the editor needs.
- **Glance works.** You can open `http://<container-ip>:8080` and see the "Startpage".
- **You have an HTTPS reverse proxy**, or can set one up: Nginx Proxy Manager, Caddy, nginx, Traefik, …
  The editor's login only works over HTTPS (see step 8).
- **Note the IPs.** You need the container's IP (in the examples, `192.168.1.10`) and the proxy's IP
  (in the examples, `192.168.1.5`). Replace them with yours throughout.

Every command below runs **as root inside the Glance container** unless it says otherwise.

## 1. Open a shell in the container

On the Proxmox host's shell (web UI → your node → **Shell**), enter the container. Replace `105` with your
Glance container's ID:

```bash
pct enter 105
```

Good: the prompt changes to `root@glance:~#`. (The container's own **Console** in the web UI works too.)

## 2. Check the Glance install

```bash
systemctl cat glance | grep ExecStart
```

Good:

```
ExecStart=/opt/glance/glance --config /opt/glance_data/glance.yml
```

That tells you two things: the Glance binary is `/opt/glance/glance`, and your **data directory** (the folder
that holds `glance.yml`) is `/opt/glance_data`. If your `--config` path is different, use that folder from
now on.

Check that there's room. The editor needs about 150 MB free:

```bash
df -h /
```

Good: `Avail` shows at least `150M`. The helper script's default 2 GB disk is plenty.

## 3. Download the editor

```bash
cd /root
curl -fsSL https://github.com/wevibevasxal/glance-bookmarks-editor/releases/latest/download/glance-bookmarks-editor.tar.gz | tar xz
ls glance-bookmarks-editor
```

Good: the list includes `install.sh`, `app`, `examples` and `docs`.

## 4. Back up your config

```bash
cp -a /opt/glance_data /root/glance_data.backup-$(date +%F)
ls /root
```

Good: you see `glance_data.backup-<today>`. To undo everything in step 5, run
`cp -a /root/glance_data.backup-<date>/. /opt/glance_data/` and `systemctl restart glance`.

## 5. Lay out the config

The editor needs the config split into a few files: the Home page in `pages/home.yml`, with your tiles in one
or more `bookmarks` widgets, and an `assets/` folder for icons.
[Config layout](config-layout.md) explains each file and why it's split this way.

### 5a. Fresh install (you haven't changed `glance.yml`)

Copy the starter config that comes with the editor:

```bash
cd /opt/glance_data
mkdir -p pages widgets local assets/icons
cp /root/glance-bookmarks-editor/examples/glance_data/glance.yml .
cp /root/glance-bookmarks-editor/examples/glance_data/pages/home.yml pages/
cp /root/glance-bookmarks-editor/examples/glance_data/widgets/countdown.yml widgets/
cp /root/glance-bookmarks-editor/examples/glance_data/local/countdown.yml local/
find . -type f | sort
```

Good:

```
./glance.yml
./local/countdown.yml
./pages/home.yml
./widgets/countdown.yml
```

This replaces the helper's "Startpage" with a **Home** page: a calendar, search, two bookmarks widgets
(Bookmarks and Apps) and a Countdown. The starter links (GitHub, Wikipedia and a "Glance Bookmarks" tile) are placeholders; you'll
change them in the editor. Continue with [step 6](#6-check-the-config-and-restart-glance).

### 5b. You already changed `glance.yml`

Keep your config and reshape it. Open a second shell (or the PVE web UI) so you can look at
`/root/glance-bookmarks-editor/examples/glance_data/` while you edit.

1. **Create the folders:**

   ```bash
   cd /opt/glance_data
   mkdir -p pages widgets local assets/icons
   ```

2. **Set the assets path.** Open `glance.yml`:

   ```bash
   nano glance.yml
   ```

   Add (or change) the `server:` section at the top. Keep the port you use today; 8080 is the default:

   ```yaml
   server:
     port: 8080
     assets-path: /opt/glance_data/assets
   ```

   Save with `Ctrl+O`, `Enter`, and exit with `Ctrl+X`. If you already had an `assets-path` pointing
   somewhere else, move those files into `/opt/glance_data/assets/` first (`mv <old path>/* /opt/glance_data/assets/`).

3. **Move the page with your tiles into `pages/home.yml`.** In `glance.yml`, under `pages:`, find the page
   your bookmarks are on (the helper calls it `- name: Startpage`). Cut that whole page: from its
   `- name:` line down to the line before the next `- name:` (or the end of the file). In nano, put the cursor
   on the `- name:` line and press `Ctrl+K` once per line; the lines go to nano's cut buffer. Then, in the same
   place, type the include line instead:

   ```yaml
   pages:
     - $include: pages/home.yml
   ```

   Save and exit. Now create the page file and paste:

   ```bash
   nano pages/home.yml
   ```

   Press `Ctrl+U` to paste. The page was indented under `pages:` in `glance.yml`, so it now starts two spaces
   too far right. Remove two spaces from the start of **every** line, so that `- name:` starts at the very
   left. Save and exit.

   **Your other pages** can stay where they are in `glance.yml`, or move to their own files the same way
   (`pages/news.yml`, with `- $include: pages/news.yml` in `glance.yml`). Separate files are easier to read;
   the editor doesn't care.

4. **Check your bookmarks widgets.** `pages/home.yml` needs at least one `type: bookmarks` widget; the helper's
   page already has one. Titles don't matter, and a widget without a title is fine. If you have several, put
   the ones you want to edit directly after each other in the same column (comments and blank lines between
   them are fine, other widgets are not), each starting with a `- type: bookmarks` line. You can add more
   bookmarks widgets later in the editor, as sections. Use spaces, never tabs.

5. **Optional: add the Countdown.** Add `- $include: ../widgets/countdown.yml` as a widget in any column,
   then copy the widget and its dates file:

   ```bash
   cp /root/glance-bookmarks-editor/examples/glance_data/widgets/countdown.yml widgets/
   cp /root/glance-bookmarks-editor/examples/glance_data/local/countdown.yml local/
   ```

   Skip this if you don't want a Countdown; the editor's Countdown tab then simply shows a note.

**Example.** Here is the helper's default page after steps 3 to 5. Its bookmarks widget is unchanged, and a
second column holds the Countdown. A `slim` page can have at most two columns, one of them `full`:

```yaml
- name: Startpage
  width: slim
  hide-desktop-navigation: true
  center-vertically: true
  columns:
    - size: full
      widgets:
        - type: search
          autofocus: true
        - type: bookmarks
          groups:
            - title: General
              links:
                - title: Google
                  url: https://www.google.com/
                - title: Helper Scripts
                  url: https://github.com/community-scripts/ProxmoxVE
    - size: small
      widgets:
        - $include: ../widgets/countdown.yml
```

And `glance.yml` is now just:

```yaml
server:
  port: 8080
  assets-path: /opt/glance_data/assets

pages:
  - $include: pages/home.yml
```

## 6. Check the config and restart Glance

```bash
/opt/glance/glance --config /opt/glance_data/glance.yml config:validate && echo CONFIG OK
```

Good: `CONFIG OK`. Otherwise Glance prints the problem with a line number, usually indentation. Fix it, or
restore the backup from step 4.

If your config uses `${VARIABLES}` from a `.env` file, load it for the check:
`(set -a; . /opt/glance_data/.env; set +a; /opt/glance/glance --config /opt/glance_data/glance.yml config:validate) && echo CONFIG OK`.

Restart Glance, and check that it's running:

```bash
systemctl restart glance
systemctl is-active glance
```

Good: `active`. Open `http://192.168.1.10:8080` in your browser: you see the Home page with your
tiles.

## 7. Install the editor

Install Python, then preview what the installer will change. The preview changes nothing:

```bash
apt-get update && apt-get install -y python3 python3-yaml
cd /root
./glance-bookmarks-editor/install.sh --plan
```

Good: it ends with `== Nothing was changed.`, after showing:

- `Moving N bookmarks widgets to local/links.yml:` and their titles (`(untitled)` for one without a title)
- the diff for `pages/home.yml`: those widgets are replaced by `- $include: ../local/links.yml`
- the new `local/links.yml`: the same widgets, unchanged
- `no old glance-bookmarks-editor unit found`
- `live config validates today`

Now install. Set the two URLs to the hostnames you'll use in step 8, and `GE_TRUSTED_PROXY` to your proxy's IP:

```bash
GE_TRUSTED_PROXY=192.168.1.5 \
GE_PUBLIC_URL=https://glance-bookmarks.example.com/ \
GE_GLANCE_URL=https://glance.example.com/ \
./glance-bookmarks-editor/install.sh --migrate
```

It backs up the config to `/root/glance-pre-editor-<date>.tar.gz`, moves the two tile widgets into
`local/links.yml`, installs the editor and starts it. A "Glance Bookmarks" tile, if there is one, is pointed at
`GE_PUBLIC_URL`. When it asks, choose the admin password (at least 12 characters) and type it twice. The user
name is `admin`.

Good: the last lines are

```
== Health check
   http://127.0.0.1:5000/health -> {"status":"ok"}
   Glance config validates

== Done. Open https://glance-bookmarks.example.com/
```

All three settings are optional and are stored in `/etc/glance-bookmarks-editor/env`; see [Reference](reference.md#settings)
to change them later. Glance's Home page looks exactly as before: the tiles now come from `local/links.yml`.

You can delete the download now: `rm -rf /root/glance-bookmarks-editor`.

## 8. Put it behind HTTPS

The editor listens on port 5000 over plain HTTP, and its login cookie is HTTPS-only. Add a hostname for it on
your reverse proxy that forwards to `http://192.168.1.10:5000`:

- **Nginx Proxy Manager:** Hosts → Proxy Hosts → Add Proxy Host.
  - Domain: `glance-bookmarks.example.com`; Scheme `http`, Forward IP `192.168.1.10`, Port `5000`.
  - Turn on **Block Common Exploits**.
  - SSL tab: pick or request a certificate, and turn on **Force SSL**.
  - Optional but recommended: an **Access List** that allows only your own devices.
- **Caddy:**

  ```
  glance-bookmarks.example.com {
      reverse_proxy 192.168.1.10:5000
  }
  ```

- **nginx:**

  ```nginx
  server {
      listen 443 ssl;
      server_name glance-bookmarks.example.com;
      # ssl_certificate ...; ssl_certificate_key ...;
      location / {
          proxy_pass http://192.168.1.10:5000;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto https;
      }
  }
  ```

Point the hostname at your proxy in DNS (or your router's local DNS), the same way as your other services.

Keep it internal. The editor runs as root; don't expose it to the internet without an extra layer such as a VPN
or an identity proxy (Cloudflare Access, Authelia, …).

No HTTPS at all? Set `GE_COOKIE_SECURE=0` in `/etc/glance-bookmarks-editor/env` and run `systemctl restart glance-bookmarks-editor`
to log in over `http://192.168.1.10:5000`. Your password then crosses the network unencrypted.

## 9. Allow port 5000 only from the proxy

So nobody can skip the proxy, let only the proxy reach port 5000. With the Proxmox firewall, run this **on the
Proxmox host** (replace `105` and the proxy IP):

```bash
cat > /etc/pve/firewall/105.fw <<'EOF'
[OPTIONS]
enable: 1
policy_in: ACCEPT

[RULES]
IN ACCEPT -source 192.168.1.5 -p tcp -dport 5000 # reverse proxy -> glance-bookmarks-editor
IN DROP -p tcp -dport 5000 # nobody else
EOF
```

`policy_in: ACCEPT` keeps everything else (Glance's 8080, SSH) working as before. Then, in the web UI, open the
container → **Network** → edit `net0` → tick **Firewall**. The rules take effect only if the firewall is also
enabled at **Datacenter → Firewall → Options**. If it isn't, read the
[Proxmox firewall docs](https://pve.proxmox.com/wiki/Firewall) before you enable it: its default inbound
policy is DROP, so you need ACCEPT rules for the web UI (8006) and SSH first.

Check from a machine that is **not** the proxy:

```bash
curl -m 5 http://192.168.1.10:5000/health
```

Good: it times out. From the proxy, it returns `{"status":"ok"}`.

## 10. Sign in and test

1. Open `https://glance-bookmarks.example.com/` and sign in as `admin`.
2. On the first section's tab, click **+ Tile**: label `Proxmox`, URL `https://192.168.1.2:8006/`, and in the
   icon search type `proxmox` and pick the icon. Click **Add**, then **Save** at the bottom.
   Good: `Tiles saved · Glance reloaded`.
3. Reload Glance's Home page: the new tile is there, with its icon.

Next: [Using the editor](using.md) · [Maintenance](maintenance.md) (updates, password reset, restore, uninstall).
