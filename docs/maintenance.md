# Maintenance

Every command runs as root inside the Glance container (`pct enter <ctid>` on the Proxmox host).

## Update the editor

```bash
cd /root
rm -rf /root/glance-bookmarks-editor
curl -fsSL https://github.com/wevibevasxal/glance-bookmarks-editor/releases/latest/download/glance-bookmarks-editor.tar.gz | tar xz
./glance-bookmarks-editor/install.sh
rm -rf /root/glance-bookmarks-editor
```

Good: it ends with `{"status":"ok"}` and `== Done.`. An update keeps your settings (`/etc/glance-bookmarks-editor/env`), your
password and everything in `local/`. Check the version with `glance-bookmarks-editor version`.

## Reset the password

```bash
glance-bookmarks-editor passwd
```

Type the new password twice; it isn't shown. This also signs out every open session.

## Restore an older version

- **In the editor:** use the History tab and click **Restore**.
- **By hand:** older versions are in `/opt/glance_data/local/.history/`, named `<file>.<date>-<time>`:

  ```bash
  ls /opt/glance_data/local/.history/
  cp /opt/glance_data/local/.history/links.yml.20261118-142500 /opt/glance_data/local/links.yml
  ```

  Glance reloads within a second.
- **Everything from before the editor was installed:** `install.sh --migrate` saved
  `/root/glance-pre-editor-<date>.tar.gz`.

## Change a setting

```bash
nano /etc/glance-bookmarks-editor/env
systemctl restart glance-bookmarks-editor
```

The settings are listed in [Reference](reference.md#settings).

## Edit the config by hand (or with other tools)

You can keep editing pages, widgets and the theme as before. Glance reloads on save. The editor owns two files,
so:

- keep the `- $include: ../local/links.yml` line in `pages/home.yml`, and the `$include: ../local/countdown.yml`
  line in `widgets/countdown.yml`
- don't overwrite or delete anything in `local/`. A tool that deploys your config should skip that folder

To edit the tiles by hand anyway, edit `local/links.yml`. The editor keeps your changes, but not your comments.

Check the config after a hand edit:

```bash
glance-bookmarks-editor validate
```

## Back up and rebuild

`/opt/glance_data` is everything: config, icons and `local/` (your tiles, dates and history). Back it up
like any other data, for example with your Proxmox backups of the container.

To rebuild the container from scratch:

1. Create Glance again with the helper script.
2. Restore `/opt/glance_data`, including `local/`.
3. Run steps 3 and 7 of the [setup guide](setup-guide.md): download the editor and run `install.sh`. The
   migration is skipped, because `pages/home.yml` already has the include line. Set the password with
   `glance-bookmarks-editor passwd`.

## Logs and troubleshooting

```bash
systemctl status glance-bookmarks-editor
journalctl -u glance-bookmarks-editor -n 50
tail -n 50 /var/log/glance-bookmarks-editor/glance-bookmarks-editor.log
curl -s http://127.0.0.1:5000/health
```

- **The login page keeps returning to sign-in.** You're on plain HTTP. Use the HTTPS hostname, or see
  `GE_COOKIE_SECURE` in [Reference](reference.md#settings).
- **"too many failed logins".** 5 failed logins in 5 minutes from one IP. Wait 5 minutes. If everyone behind the
  proxy gets blocked at once, `GE_TRUSTED_PROXY` doesn't match the proxy's IP.
- **A save says "Glance didn't log a reload".** The file was saved, but Glance didn't confirm the reload
  within 3 s. Check `journalctl -u glance -n 20`.
- **Icon search finds nothing.** The container needs outbound HTTPS to `raw.githubusercontent.com`. Test it
  with `curl -sI https://raw.githubusercontent.com/selfhst/icons/main/index.json`.

`GET /health` returns `{"status":"ok"}` without a login. Use it for uptime monitors such as Uptime Kuma.

## Uninstall

The tiles keep working without the editor: Glance still reads them from `local/links.yml`.

```bash
systemctl disable --now glance-bookmarks-editor
rm -rf /opt/glance-bookmarks-editor /etc/glance-bookmarks-editor /var/cache/glance-bookmarks-editor /var/log/glance-bookmarks-editor \
       /etc/systemd/system/glance-bookmarks-editor.service /usr/local/sbin/glance-bookmarks-editor /usr/sbin/glance-bookmarks-editor
systemctl daemon-reload
```

Also remove the proxy host, and the firewall rule for port 5000.
