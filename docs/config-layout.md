# Config layout

The editor needs your Glance config split into a few files, so that the parts it edits live in files it owns.
This page explains the layout and why it's set up that way. The [setup guide](setup-guide.md) has the commands
to get there.

## Target layout

```
<data dir>/
├── glance.yml
├── .env
├── pages/
│   ├── home.yml
│   └── <other pages>.yml
├── widgets/
│   └── countdown.yml
├── local/
│   ├── links.yml
│   └── countdown.yml
└── assets/
    ├── icons/
    └── custom.css
```

`<data dir>` is the folder that holds your `glance.yml`. The Proxmox VE helper script uses `/opt/glance_data`,
which is also the editor's default; for any other folder, tell the installer with `GE_DATA`.

| Path | What it is | Who edits it |
|---|---|---|
| `glance.yml` | Server, theme and branding settings, plus the list of pages as `$include` lines | you |
| `.env` | Optional. Secrets used as `${VAR}` in the config (root, mode 600) | you |
| `pages/home.yml` | The Home page: its columns and widgets. Where the tiles appear is set here | you |
| `pages/<other>.yml` | Optional. Your other pages, one file each, or keep them inline in `glance.yml` | you |
| `widgets/countdown.yml` | Optional. The Countdown widget's *definition*: title, layout, HTML template | you |
| `local/links.yml` | Your Home page's bookmarks widgets (the editor calls them sections) with all their groups and links. Created by `install.sh --migrate` | **the editor** |
| `local/countdown.yml` | Optional. The Countdown *dates* (birthdays and events) | **the editor** |
| `local/.history/` | The last 30 versions of the two files above | **the editor** |
| `assets/icons/` | Tile icons. Created on the first download | **the editor** (and you, for your own SVGs) |
| `assets/custom.css` | Optional. Your `custom-css-file` | you |

## Why it's split this way

The layout separates **how the dashboard looks** from **what's on it**:

- **Everything outside `local/` is structure.** That's pages, columns, widget types, templates and theme. You
  change it rarely, by hand or with whatever tool you use to manage config: a script, a git repo you deploy
  from, an AI assistant.
- **Everything inside `local/` is content.** That's the links and the dates. You change it often, through the
  editor. The editor writes only inside `local/` and `assets/icons/`, and never touches your other files.

This split gives you:

- **No collisions.** You (or a script, or an AI agent) can rewrite pages, the theme or other widgets without
  wiping out links you added in the editor. In return, the editor can rewrite its own files on every save
  without mangling your comments and formatting elsewhere. The one rule: whatever manages your config must
  **leave `local/` alone** and keep the `$include` lines that point into it.
- **Easy backup and rebuild.** `local/` holds all your personal data, so it's the one folder you must never lose.
  The structure can be regenerated or restored from wherever you keep it.
- **Safe saves.** Because the editor owns whole files, it can validate a new version, keep the old one in
  History and swap it in atomically, without editing your YAML in place.

**Why does Countdown show up twice?** For the same reason. `widgets/countdown.yml` is the widget itself, the
structure: its title and the template that turns dates into "in 12 days". It pulls its options in from
`local/countdown.yml`, which is only the list of dates you edit in the editor. You can restyle the widget freely
without touching your dates, and adding a birthday never touches the template.

**How the pieces are joined.** Glance's
[`$include`](https://github.com/glanceapp/glance/blob/main/docs/configuration.md#including-other-config-files)
pastes one file into another before the config is read:

```
glance.yml
└─ pages/home.yml
   ├─ local/links.yml
   └─ widgets/countdown.yml
      └─ local/countdown.yml
```

That is: `glance.yml` includes the Home page. The Home page includes the tiles (`- $include: ../local/links.yml`)
and the Countdown widget (`- $include: ../widgets/countdown.yml`), and the widget includes its dates
(`$include: ../local/countdown.yml` under `options:`).

The included text is indented to where the `$include` line sits. Paths are relative to the file that contains
the line, which is why the page files use `../`. A missing file stops the whole config from loading.

## The rules the editor relies on

- **The Home page is in `pages/home.yml`.** That file name is fixed. `glance.yml` includes it with
  `- $include: pages/home.yml`. The page itself can have any name.
- **At least one bookmarks widget, in one place.** Before the migration, `pages/home.yml` has one or more
  `type: bookmarks` widgets, with any titles (or none). The ones the editor should manage sit directly after
  each other in the same column; comments and blank lines between them are fine, other widgets are not. Each
  starts with `- type: bookmarks` on its first line. `install.sh --migrate` moves them into `local/links.yml`
  and leaves `- $include: ../local/links.yml` in their place. If bookmarks widgets sit in more than one place,
  the biggest set moves and the others stay where they are (the installer lists them); the editor doesn't
  manage those. Any other widgets on the page are left alone. After the migration you add, rename, reorder and
  delete bookmarks widgets in the editor, as sections. They all appear where the include line is.
- **`assets-path` is `<data dir>/assets`.** The editor saves icons to `assets/icons/<name>.webp` and links them
  as `/assets/icons/<name>.webp`.
- **Countdown keys.** If you use the Countdown, `local/countdown.yml` holds exactly `birthday-1..3`
  (`"MM-DD|Name"`) and `event-1..5` (`"MM-DD|Name|emoji"` every year, or `"YYYY-MM-DD|Name|emoji"` once).
  The example widget template reads these keys and no others. You can restyle the template, as long as it
  reads the same keys.
- **Other pages** can stay inline under `pages:` in `glance.yml`, or move to `pages/<name>.yml` and be included
  the same way as home. Separate files are recommended: each page is easier to read and edit, and `glance.yml`
  stays a short list of settings and pages.
- **Whatever else manages your config** (you, a script, a git repo you deploy from, an AI agent) must keep the
  `$include` lines that point into `local/`, and must never overwrite or ship `local/`.

A complete config in this layout is in [`examples/glance_data/`](../examples/glance_data).
