# Using the editor

Open the editor's URL (for example `https://glance-bookmarks.example.com/`) and sign in as `admin`. The tabs along
the top are **Tiles**, **Countdown**, **Icons** and **History**. If `GE_GLANCE_URL` is set, an **Open Glance**
link sits on the right.

Changes stay in your browser until you press **Save** in the bar at the bottom. **Discard** throws them away.
Every save is checked by Glance first: if Glance rejects it, nothing is written, and you see Glance's error.
After a successful save the bar says `… saved · Glance reloaded`, and the dashboard shows the change within
about a second.

## Tiles

- **Sections.** Each bookmarks widget on your Home page is a section, with its own tab (named after the widget's
  title). **+ Section** adds a new bookmarks widget; **✎** and **✕** next to the section name rename or delete
  it. Drag a tab to change the order of the widgets on the page. The last section can't be deleted. A new section
  starts with no groups: add one with **+ Group** before you add tiles.
- Each group is a card, and each link is a row with its icon, label and URL.
- **+ Tile** opens the form:
  - **Label**
  - **URL** (must start with `http://` or `https://`)
  - **Icon** (see below)
  - **Group**: any group in any section, so this is also how you move a tile to another section
  - **Advanced:** `description` (shown under the label) and `same-tab`; it defaults to the group's setting.
- Edit or delete a tile with the buttons on its row.
- **Drag** the handle on a row to reorder tiles or move them to another group. Drag a card's handle to reorder
  groups.
- **+ Group** adds a group. Rename or delete a group from its card. Deleting a group that still has tiles asks
  first.

## Icons

- **Search selfh.st:** in the tile form, type a name, e.g. `jellyfin` or `proxmox`. Previews come straight from
  [selfh.st/icons](https://selfh.st/icons). Where one exists, the `-light` variant is shown next to the
  normal one. Pick one; the server downloads it to `assets/icons/<name>.webp` when you click **Add** or **Update**.
- **Choose an existing icon:** pick any file already in `assets/icons/`. This includes your own SVG or PNG
  files, which you copy into that folder by hand. The editor itself never takes uploads or arbitrary URLs.
- **Icons tab:** every file in `assets/icons/` with a "used by" count.
  - **Re-download** fetches the latest version of a selfh.st icon.
  - **Delete** works only for icons nothing uses.
  - Browsers cache Glance's icons for a while. Hard-refresh the dashboard if a re-downloaded icon doesn't
    change.

## Countdown

- **Birthdays:** up to 3, each with a name and a month and day. They repeat every year.
- **Events:** up to 5, each with a name, a date, an emoji, and **yearly** or **one time**. A one-time event
  disappears from the dashboard after its date.
- Names and emoji can't contain `|` or `"`. The counter shows how many slots are left.

This tab needs the Countdown widget on your Home page; see [Config layout](config-layout.md). Without it, the tab
shows a note, and the rest of the editor works as usual.

## History

The last 30 saved versions of the tiles (`local/links.yml`) and of the countdown (`local/countdown.yml`).

- Open a version to see a diff against the current file.
- Click **Restore** to bring it back. A restore is a save like any other, so it's validated, and it can be undone
  from History in turn.
- If the version uses icon files that are no longer in the library, the editor warns you.
