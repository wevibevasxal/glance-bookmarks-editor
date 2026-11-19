#!/usr/bin/env python3
"""glance-bookmarks-editor command line.

  glance-bookmarks-editor serve                 run the web editor (systemd does this)
  glance-bookmarks-editor passwd                set the admin password (hidden prompt; never an argument)
  glance-bookmarks-editor migrate --plan DIR    write the migration diff to DIR, change nothing
  glance-bookmarks-editor migrate --apply       move the Home page's bookmarks widgets into local/links.yml
  glance-bookmarks-editor validate              run glance config:validate on the live config
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from glance_bookmarks_editor import __version__, auth, migrate, pipeline, settings  # noqa: E402


def cmd_passwd(args):
    if args.stdin:
        pw = sys.stdin.readline().rstrip("\n")
        pw2 = pw
    else:
        if not sys.stdin.isatty():
            print("glance-bookmarks-editor passwd needs a terminal (open a root shell on the Glance host, then run: glance-bookmarks-editor passwd)", file=sys.stderr)
            return 2
        pw = getpass.getpass("New glance-bookmarks-editor password: ")
        pw2 = getpass.getpass("Repeat it: ")
    if pw != pw2:
        print("The passwords don't match; nothing changed.", file=sys.stderr)
        return 1
    if len(pw) < auth.MIN_PASSWORD:
        print(f"Use at least {auth.MIN_PASSWORD} characters; nothing changed.", file=sys.stderr)
        return 1
    settings.write_setting("GE_PASSWORD_HASH", auth.hash_password(pw))
    print(f"Password saved to {settings.SETTINGS_FILE} (PBKDF2-SHA256, {auth.ITERATIONS} iterations). "
          "Existing sessions are signed out.")
    return 0


def cmd_migrate(args):
    try:
        p = migrate.plan(args.public_url)
    except (migrate.MigrationError, OSError) as e:
        print(f"MIGRATION STOPPED: {e}", file=sys.stderr)
        return 3
    if p["already"]:
        print("pages/home.yml already includes ../local/links.yml — widget move skipped.")
    else:
        moved = p.get("moved") or []
        print(f"Moving {len(moved)} bookmarks widget{'s' if len(moved) != 1 else ''} to local/links.yml: "
              + ", ".join(moved))
        if p.get("left"):
            print(f"NOTE: {p['left']} other bookmarks widget(s) elsewhere in pages/home.yml stay where they are; "
                  "the editor won't manage them. To manage them too, move them next to the others and run "
                  "this before installing, or recreate them in the editor as new sections.")
    home_diff = migrate.diff(p["home_old"], p["home_new"], "a/pages/home.yml", "b/pages/home.yml")
    links_diff = migrate.diff(p.get("links_old"), p.get("links_new"), "a/local/links.yml", "b/local/links.yml")
    if p.get("tile_old_url"):
        print(f"Glance Bookmarks tile: {p['tile_old_url']} -> {args.public_url or settings.get('GE_PUBLIC_URL')}")
    elif not p["already"] and not p.get("tile_found") and (args.public_url or settings.get("GE_PUBLIC_URL")):
        print("NOTE: no 'Glance Bookmarks' tile found; nothing to repoint.")
    if args.plan:
        os.makedirs(args.plan, exist_ok=True)
        for name, text in (("home.yml.diff", home_diff), ("links.yml.diff", links_diff),
                           ("home.yml.new", p["home_new"]), ("links.yml.new", p.get("links_new") or "")):
            with open(os.path.join(args.plan, name), "w", encoding="utf-8") as f:
                f.write(text)
        print(f"Plan written to {args.plan} (nothing changed). Equivalence check passed.")
        return 0
    if args.apply:
        try:
            for a in migrate.apply(p):
                print(" -", a)
        except migrate.MigrationError as e:
            print(f"MIGRATION FAILED: {e}", file=sys.stderr)
            return 4
        return 0
    sys.stdout.write(home_diff + links_diff)
    return 0


def cmd_validate(args):
    ok, msg = pipeline.validate_config(live=True)
    print(("valid" if ok else "INVALID") + (f": {msg}" if msg else ""))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="glance-bookmarks-editor", description=f"glance-bookmarks-editor {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    p = sub.add_parser("passwd")
    p.add_argument("--stdin", action="store_true", help="read one line from stdin (for scripts and tests)")
    m = sub.add_parser("migrate")
    g = m.add_mutually_exclusive_group()
    g.add_argument("--plan", metavar="DIR")
    g.add_argument("--apply", action="store_true")
    m.add_argument("--public-url")
    sub.add_parser("validate")
    sub.add_parser("version")
    a = ap.parse_args()
    if a.cmd == "serve":
        from glance_bookmarks_editor import server
        server.serve()
        return 0
    if a.cmd == "version":
        print(__version__)
        return 0
    return {"passwd": cmd_passwd, "migrate": cmd_migrate, "validate": cmd_validate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
