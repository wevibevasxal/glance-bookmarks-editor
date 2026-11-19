"""Links (the Home page's bookmarks widgets) and Countdown: parse, validate, and write with a stable layout."""

import datetime
import os
import re
import urllib.parse

import yaml

from . import pipeline, settings

LINKS = "local/links.yml"
COUNTDOWN = "local/countdown.yml"
MAX_WIDGETS = 20
MAX_BIRTHDAYS = 3
MAX_EVENTS = 5

LINKS_HEADER = """\
# Home page tiles: one list item per bookmarks widget (section), in page order.
# Included by pages/home.yml with "- $include: ../local/links.yml", where the widgets used to be.
# Managed by glance-bookmarks-editor. Hand edits are kept,
# but comments are not: the editor rewrites this file on every save.
"""

COUNTDOWN_HEADER = """\
# Countdown dates, included into the Countdown widget's options: (widgets/countdown.yml).
# Managed by glance-bookmarks-editor; it rewrites this file on save.
#   birthday-1..3: "MM-DD|Name"              every year
#   event-1..5:    "MM-DD|Name|emoji"        every year
#                  "YYYY-MM-DD|Name|emoji"   once; hidden after the date
# Keep the quotes. Never delete or rename this file: Glance stops loading without it.
"""


class ModelError(Exception):
    def __init__(self, message, where=""):
        super().__init__(f"{where}: {message}" if where else message)


# ---------------------------------------------------------------- text rules

_CTRL = re.compile(r"[\x00-\x1f\x7f  ]")
_KEY = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
_COLOR = re.compile(r"^\d{1,3} \d{1,3} \d{1,3}$")
ICON_PREFIX = "/assets/icons/"
_ICON_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def check_text(value, where, maxlen, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ModelError("must be text", where)
    value = value.strip()
    if required and not value:
        raise ModelError("is required", where)
    if len(value) > maxlen:
        raise ModelError(f"is longer than {maxlen} characters", where)
    if _CTRL.search(value):
        raise ModelError("contains a line break or control character", where)
    if "${" in value:
        # Glance would substitute it (possibly with a secret from .env) before parsing.
        raise ModelError("must not contain ${", where)
    return value


def check_url(value, where):
    value = check_text(value, where, 2000, required=True)
    if re.search(r"\s", value):
        raise ModelError("must not contain spaces", where)
    u = urllib.parse.urlsplit(value)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ModelError("must start with http:// or https:// and have a host", where)
    return value


def _bool(value, where):
    if not isinstance(value, bool):
        raise ModelError("must be true or false", where)
    return value


def _extras(obj, known, where):
    """Unknown keys are passed through if they are simple scalars (keeps hand-added settings)."""
    out = {}
    for k, v in obj.items():
        if k in known:
            continue
        if not isinstance(k, str) or not _KEY.match(k):
            raise ModelError(f"unexpected key {k!r}", where)
        if isinstance(v, str):
            v = check_text(v, f"{where} {k}", 500)
        elif not isinstance(v, (bool, int, float)) or v is None:
            raise ModelError(f"{k} must be a simple value", where)
        out[k] = v
    return out


# ---------------------------------------------------------------- YAML output

class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):  # indent lists under their key
        return super().increase_indent(flow, False)


def _dump(obj):
    return yaml.dump(obj, Dumper=_Dumper, sort_keys=False, indent=2, default_flow_style=False,
                     allow_unicode=True, width=100000)


# ---------------------------------------------------------------- links

LINK_KEYS = ("title", "url", "icon", "description", "same-tab")
GROUP_KEYS = ("title", "color", "same-tab", "hide-arrow", "links")
WIDGET_KEYS = ("type", "title", "groups")


def icons_dir():
    return os.path.join(settings.data_dir(), "assets", "icons")


def icon_file(icon):
    """'/assets/icons/x.webp' -> 'x.webp', else None."""
    if isinstance(icon, str) and icon.startswith(ICON_PREFIX):
        name = icon[len(ICON_PREFIX):]
        if _ICON_FILE.match(name) and ".." not in name:
            return name
    return None


def _all_icons(widgets):
    out = set()
    for w in widgets or []:
        for g in (w.get("groups") or []) if isinstance(w, dict) else []:
            for link in (g.get("links") or []) if isinstance(g, dict) else []:
                if isinstance(link, dict) and isinstance(link.get("icon"), str):
                    out.add(link["icon"])
    return out


def load_links():
    data, digest = pipeline.read(LINKS)
    if data is None:
        raise ModelError("local/links.yml doesn't exist yet: run install.sh --migrate first")
    try:
        widgets = yaml.safe_load(data.decode("utf-8"))
    except yaml.YAMLError as e:
        raise ModelError(f"local/links.yml isn't valid YAML: {e}")
    if (not isinstance(widgets, list) or not widgets
            or not all(isinstance(w, dict) and w.get("type") == "bookmarks" for w in widgets)):
        raise ModelError("local/links.yml must be a list of one or more `type: bookmarks` widgets")
    for w in widgets:
        w.setdefault("groups", [])
        for g in w["groups"] or []:
            if isinstance(g, dict):
                g.setdefault("links", [])
                if g["links"] is None:
                    g["links"] = []
    return {"sha": digest, "widgets": widgets}


def normalize_links(widgets, current_widgets):
    """Validate client state and return the clean structure, in stable key order."""
    if not isinstance(widgets, list) or not widgets:
        raise ModelError("keep at least one section (bookmarks widget)")
    if len(widgets) > MAX_WIDGETS:
        raise ModelError(f"at most {MAX_WIDGETS} sections")
    keep_icons = _all_icons(current_widgets)   # non-library icons (si:, di:, URLs) are kept, not added
    out = []
    for wi, w in enumerate(widgets):
        wname = f"Section {wi + 1}"
        if not isinstance(w, dict) or w.get("type") != "bookmarks":
            raise ModelError("must be a bookmarks widget", wname)
        nw = {"type": "bookmarks"}
        title = check_text(w.get("title"), wname + " title", 80)
        if title:                       # untitled is allowed: Glance shows its default header
            nw["title"] = title
            wname = title
        groups = w.get("groups") or []
        if not isinstance(groups, list):
            raise ModelError("groups must be a list", wname)
        if len(groups) > 50:
            raise ModelError("too many groups", wname)
        ngroups = []
        for gi, g in enumerate(groups):
            gw = f"{wname} › group {gi + 1}"
            if not isinstance(g, dict):
                raise ModelError("must be a group", gw)
            ng = {}
            gtitle = check_text(g.get("title"), gw + " title", 80)
            if gtitle:                  # untitled groups are allowed too
                ng["title"] = gtitle
                gw = f"{wname} › {gtitle}"
            if g.get("color") not in (None, ""):
                c = check_text(g.get("color"), gw + " color", 20)
                if not _COLOR.match(c):
                    raise ModelError("color must look like '24 97 58' (hue saturation lightness)", gw)
                ng["color"] = c
            for k in ("same-tab", "hide-arrow"):
                if g.get(k) is not None:
                    ng[k] = _bool(g[k], f"{gw} {k}")
            links = g.get("links") or []
            if not isinstance(links, list) or len(links) > 200:
                raise ModelError("links must be a list (max 200)", gw)
            nlinks = []
            for li, link in enumerate(links):
                lw = f"{gw} › link {li + 1}"
                if not isinstance(link, dict):
                    raise ModelError("must be a link", lw)
                nl = {"title": check_text(link.get("title"), lw + " label", 100, required=True)}
                lw = f"{gw} › {nl['title']}"
                nl["url"] = check_url(link.get("url"), lw + " URL")
                icon = link.get("icon")
                if icon not in (None, ""):
                    icon = check_text(icon, lw + " icon", 500)
                    f = icon_file(icon)
                    if f:
                        p = os.path.join(icons_dir(), f)
                        if not os.path.isfile(p) or os.path.islink(p):
                            raise ModelError(f"icon file {f} isn't in assets/icons", lw)
                    elif icon not in keep_icons:
                        raise ModelError("icon must be from the icon library", lw)
                    nl["icon"] = icon
                if link.get("description") not in (None, ""):
                    nl["description"] = check_text(link.get("description"), lw + " description", 300)
                if link.get("same-tab") is not None:
                    nl["same-tab"] = _bool(link["same-tab"], lw + " same-tab")
                nl.update(_extras(link, LINK_KEYS, lw))
                nlinks.append(nl)
            ng.update(_extras(g, GROUP_KEYS, gw))
            ng["links"] = nlinks
            ngroups.append(ng)
        nw.update(_extras(w, WIDGET_KEYS, wname))
        nw["groups"] = ngroups
        out.append(nw)
    return out


def render_links(widgets):
    return LINKS_HEADER + _dump(widgets)


def save_links(widgets, base_sha):
    cur = load_links()
    clean = normalize_links(widgets, cur["widgets"])
    return pipeline.save(LINKS, render_links(clean), base_sha)


# ---------------------------------------------------------------- countdown

_MD = re.compile(r"^(\d{2})-(\d{2})$")
_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_CD_KEY = re.compile(r"^(birthday|event)-(\d+)$")


def check_date(value, where, allow_year):
    value = (value or "").strip() if isinstance(value, str) else ""
    m = _MD.match(value)
    if m:
        try:
            datetime.date(2000, int(m.group(1)), int(m.group(2)))  # leap year: 02-29 is fine
        except ValueError:
            raise ModelError("isn't a real date", where)
        return value
    m = _YMD.match(value)
    if m and allow_year:
        try:
            datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            raise ModelError("isn't a real date", where)
        return value
    raise ModelError("must be MM-DD" + (" or YYYY-MM-DD" if allow_year else ""), where)


def check_part(value, where, maxlen, required=True):
    value = check_text(value, where, maxlen, required)
    for ch in '|"\\':
        if ch in value:
            raise ModelError(f"must not contain {ch}", where)
    return value


def load_countdown():
    data, digest = pipeline.read(COUNTDOWN)
    if data is None:
        raise ModelError("local/countdown.yml is missing. If a Countdown widget includes it, Glance can't "
                         "load until it's back (restore it from History or recreate it); if you don't use "
                         "a Countdown widget, ignore this tab. See the README's Countdown section.")
    try:
        raw = yaml.safe_load(data.decode("utf-8")) or {}
    except yaml.YAMLError as e:
        raise ModelError(f"local/countdown.yml isn't valid YAML: {e}")
    if not isinstance(raw, dict):
        raise ModelError("local/countdown.yml must be a list of key: value lines")
    birthdays, events, other, problems = [], [], {}, []
    for key in sorted(raw, key=lambda k: (str(k).split("-")[0], int(_CD_KEY.match(str(k)).group(2))
                                          if _CD_KEY.match(str(k)) else 0)):
        val = raw[key]
        m = _CD_KEY.match(str(key))
        if not m:
            other[str(key)] = val
            continue
        parts = str(val if val is not None else "").split("|")
        if m.group(1) == "birthday":
            birthdays.append({"date": parts[0].strip(), "name": "|".join(parts[1:]).strip()})
        else:
            events.append({"date": parts[0].strip(), "name": (parts[1] if len(parts) > 1 else "").strip(),
                           "emoji": "|".join(parts[2:]).strip()})
    for i, b in enumerate(birthdays):
        try:
            check_date(b["date"], "", False)
        except ModelError:
            problems.append(f"Birthday {i + 1} ({b['name'] or '?'}) has a date the editor can't read: {b['date']!r}")
    for i, e in enumerate(events):
        try:
            check_date(e["date"], "", True)
        except ModelError:
            problems.append(f"Event {i + 1} ({e['name'] or '?'}) has a date the editor can't read: {e['date']!r}")
    return {"sha": digest, "birthdays": birthdays, "events": events, "other": other, "problems": problems,
            "max_birthdays": MAX_BIRTHDAYS, "max_events": MAX_EVENTS}


def render_countdown(birthdays, events, other=None):
    if not isinstance(birthdays, list) or not isinstance(events, list):
        raise ModelError("expected lists of birthdays and events")
    if len(birthdays) > MAX_BIRTHDAYS:
        raise ModelError(f"at most {MAX_BIRTHDAYS} birthdays")
    if len(events) > MAX_EVENTS:
        raise ModelError(f"at most {MAX_EVENTS} events")
    lines = []
    for i, b in enumerate(birthdays, 1):
        w = f"Birthday {i}"
        if not isinstance(b, dict):
            raise ModelError("bad entry", w)
        d = check_date(b.get("date"), w + " date", False)
        n = check_part(b.get("name"), w + " name", 40)
        lines.append(f'birthday-{i}: "{d}|{n}"')
    for i, e in enumerate(events, 1):
        w = f"Event {i}"
        if not isinstance(e, dict):
            raise ModelError("bad entry", w)
        d = check_date(e.get("date"), w + " date", True)
        n = check_part(e.get("name"), w + " name", 40)
        em = check_part(e.get("emoji"), w + " emoji", 16)
        lines.append(f'event-{i}: "{d}|{n}|{em}"')
    for k, v in (other or {}).items():  # keys the editor doesn't manage are kept as they were
        if not _KEY.match(str(k)) or _CD_KEY.match(str(k)):
            continue
        lines.append(_dump({k: v}).rstrip("\n"))
    body = "\n".join(lines) + "\n" if lines else ""
    return COUNTDOWN_HEADER + body


def save_countdown(birthdays, events, base_sha):
    cur = load_countdown()
    return pipeline.save(COUNTDOWN, render_countdown(birthdays, events, cur["other"]), base_sha)
