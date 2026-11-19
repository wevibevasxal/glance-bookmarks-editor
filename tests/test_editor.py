"""End-to-end tests against a temp copy of tests/fixture with the real glance 0.8.7 binary.

Run:  tests/get-glance.sh && python3 -m unittest discover -s tests -v
Covers the brief's test plan (§6) except the systemd/journal/install parts, which tests/container/run.sh covers.
"""

import hashlib
import http.client
import http.server
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
GLANCE = os.path.join(HERE, "bin", "glance")
SECRET = "s3cret-Example-value-42"
PASSWORD = "correct horse battery staple"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def webp_bytes(size=64):
    body = b"VP8L" + struct.pack("<I", size) + b"\0" * size
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


# ---------------------------------------------------------------- fake selfh.st

class FakeSelfhst(http.server.BaseHTTPRequestHandler):
    files = {
        "/webp/immich.webp": webp_bytes(),
        "/webp/immich-light.webp": webp_bytes(80),
        "/webp/huge.webp": webp_bytes(600 * 1024),
        "/webp/png-in-disguise.webp": b"\x89PNG\r\n\x1a\n" + b"\0" * 100,
    }
    index = [{"Name": "Immich", "Reference": "immich", "WebP": "Yes", "Light": "Yes"},
             {"Name": "Huge", "Reference": "huge", "WebP": "Yes", "Light": "No"},
             {"Name": "Bad <b>", "Reference": "../etc", "WebP": "Yes", "Light": "No"}]

    def do_GET(self):
        if self.path == "/webp/redirect.webp":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest")
            self.end_headers()
            return
        if self.path == "/index.json":
            data = json.dumps(self.index).encode()
        elif self.path in self.files:
            data = self.files[self.path]
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


# ---------------------------------------------------------------- harness

class Client:
    def __init__(self, port):
        self.port, self.cookies, self.csrf = port, {}, ""

    def req(self, method, path, body=None, headers=None, csrf=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        if method == "POST" and csrf and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = self.csrf
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                val = rest.split(";")[0]
                if "Max-Age=0" in v:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = val
        try:
            js = json.loads(raw)
        except ValueError:
            js = None
        return r.status, js, dict((k.lower(), v) for k, v in r.getheaders()), raw

    def login(self, password=PASSWORD, user="admin", headers=None):
        st, js, _, _ = self.req("GET", "/api/session", headers=headers)
        if st == 200:
            self.csrf = js["csrf"]
            return st, js
        token = js["login_csrf"]
        st, js, _, _ = self.req("POST", "/api/login", {"user": user, "password": password},
                                headers={"X-CSRF-Token": token, **(headers or {})})
        if st == 200:
            self.csrf = js["csrf"]
        return st, js


@unittest.skipUnless(os.path.exists(GLANCE), "run tests/get-glance.sh first")
class EditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ge-test-")
        cls.data = os.path.join(cls.tmp, "glance_data")
        shutil.copytree(os.path.join(HERE, "fixture", "glance_data"), cls.data)
        with open(os.path.join(cls.data, "glance.yml")) as f:
            g = f.read().replace("/opt/glance_data/assets", os.path.join(cls.data, "assets"))
        with open(os.path.join(cls.data, "glance.yml"), "w") as f:
            f.write(g)
        with open(os.path.join(cls.data, ".env"), "w") as f:
            f.write(f"EXAMPLE_API_KEY={SECRET}\n")
        cls.fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeSelfhst)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        fport = cls.fake.server_address[1]
        cls.port = free_port()
        cls.settings_file = os.path.join(cls.tmp, "env")
        os.environ.update({
            "GE_SETTINGS": cls.settings_file, "GE_DATA": cls.data, "GE_GLANCE_BIN": GLANCE,
            "GE_CACHE_DIR": os.path.join(cls.tmp, "cache"), "GE_LOG_DIR": os.path.join(cls.tmp, "log"),
            "GE_LISTEN": "127.0.0.1", "GE_PORT": str(cls.port), "GE_TRUSTED_PROXY": "192.168.1.5",
            "GE_PUBLIC_URL": "https://glance-bookmarks.example.com/",
            "GE_ICON_BASE_URL": f"http://127.0.0.1:{fport}/webp/", "GE_INDEX_URL": f"http://127.0.0.1:{fport}/index.json",
        })
        from glance_bookmarks_editor import auth, migrate, settings
        settings.SETTINGS_FILE = cls.settings_file
        settings.write_setting("GE_PASSWORD_HASH", auth.hash_password(PASSWORD, 600_000))
        # snapshot everything outside local/ and assets/icons/ (the editor must never write there)
        p = migrate.plan()
        migrate.apply(p)
        cls.plan = p
        cls.outside = cls.snapshot_outside()
        from glance_bookmarks_editor import server
        server.setup_logging()
        server.log.setLevel("ERROR")
        from wsgiref.simple_server import make_server
        cls.httpd = make_server("127.0.0.1", cls.port, server.app, server_class=server.ThreadingWSGIServer,
                                handler_class=server.QuietHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.fake.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def snapshot_outside(cls):
        out = {}
        for root, dirs, files in os.walk(cls.data):
            rel = os.path.relpath(root, cls.data)
            if rel == "local" or rel.startswith("local" + os.sep) or rel == os.path.join("assets", "icons"):
                continue
            for n in files:
                p = os.path.join(root, n)
                out[os.path.relpath(p, cls.data)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        return out

    def setUp(self):
        self.server.LIMITER.tries.clear()
        self.c = Client(self.port)
        st, _ = self.c.login()
        self.assertEqual(st, 200)

    def read(self, rel):
        with open(os.path.join(self.data, rel), encoding="utf-8") as f:
            return f.read()

    def links(self):
        st, js, _, _ = self.c.req("GET", "/api/links")
        self.assertEqual(st, 200, js)
        return js

    def save_links(self, js, expect=200):
        st, r, _, _ = self.c.req("POST", "/api/links", {"sha": js["sha"], "widgets": js["widgets"]})
        self.assertEqual(st, expect, r)
        return r

    def glance_valid(self):
        from glance_bookmarks_editor import pipeline
        ok, msg = pipeline.validate_config(live=True)
        self.assertTrue(ok, msg)

    # ------------------------------------------------------------ migration

    def test_00_migration(self):
        p = self.plan
        self.assertIn("      - $include: ../local/links.yml\n", p["home_new"])
        self.assertNotIn("type: bookmarks", p["home_new"])
        self.assertNotIn("---------- Web bookmarks", p["home_new"])      # banners move with the widgets
        self.assertIn("# ---------- Self-hosted apps ----------\n- type: bookmarks\n  title: Apps", self.read("local/links.yml"))
        self.assertIn("Keep this include line", p["home_new"])
        self.assertEqual(p["tile_old_url"], "http://192.168.1.10:5000/")
        links = yaml.safe_load(self.read("local/links.yml"))
        self.assertEqual([w["title"] for w in links], ["Bookmarks", "Apps"])
        tile = [l for g in links[1]["groups"] for l in g["links"] if l["title"] == "Glance Bookmarks"][0]
        self.assertEqual(tile["url"], "https://glance-bookmarks.example.com/")
        # widgets copied verbatim: the old home.yml blocks, dedented by 6, are in links.yml
        self.assertIn("    - title: General\n      same-tab: true\n      hide-arrow: true\n", self.read("local/links.yml"))
        from glance_bookmarks_editor import migrate
        again = migrate.plan()
        self.assertTrue(again["already"])
        self.assertEqual(migrate.apply(again), ["nothing to change (already migrated, tile already updated)"])
        self.glance_valid()

    def test_01_editor_rerender_is_stable(self):
        """Saving without changes writes nothing; the editor's layout equals the migrated layout."""
        from glance_bookmarks_editor import model
        cur = model.load_links()
        text = model.render_links(model.normalize_links(cur["widgets"], cur["widgets"]))
        # identical except the comment banners carried over from home.yml (the editor doesn't keep comments)
        strip = lambda t: [l for l in t.splitlines() if not l.lstrip().startswith("#")]
        self.assertEqual(strip(text), strip(self.read("local/links.yml")))
        r = self.save_links(self.links())
        self.assertEqual(r["status"], "saved")          # first save drops the banners, nothing else
        r = self.save_links(self.links())
        self.assertEqual(r["status"], "unchanged")

    # ------------------------------------------------------------ public + headers

    def test_health_and_headers(self):
        anon = Client(self.port)
        st, js, h, _ = anon.req("GET", "/health")
        self.assertEqual((st, js), (200, {"status": "ok"}))
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", h["content-security-policy"])
        self.assertIn("img-src 'self' https://cdn.jsdelivr.net https://raw.githubusercontent.com",
                      h["content-security-policy"])
        st, _, h, raw = anon.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"Glance Bookmarks", raw)
        for path in ("/api/links", "/api/countdown", "/api/history", "/icons/gmail.webp", "/api/icons/library"):
            self.assertEqual(anon.req("GET", path)[0], 401, path)
        self.assertEqual(anon.req("GET", "/static/../glance_bookmarks_editor/auth.py")[0], 404)

    def test_session_cookie_flags(self):
        anon = Client(self.port)
        st, js, h, _ = anon.req("GET", "/api/session")
        token = js["login_csrf"]
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/login", json.dumps({"user": "admin", "password": PASSWORD}),
                  {"Content-Type": "application/json", "X-CSRF-Token": token, "Cookie": f"ge_login={token}"})
        r = c.getresponse()
        r.read()
        cookies = [v for k, v in r.getheaders() if k.lower() == "set-cookie" and v.startswith("ge_session=")]
        self.assertEqual(r.status, 200)
        self.assertTrue(cookies)
        for flag in ("HttpOnly", "Secure", "SameSite=Strict"):
            self.assertIn(flag, cookies[0])

    # ------------------------------------------------------------ login, rate limit, CSRF, expiry

    def test_login_rate_limit(self):
        c = Client(self.port)
        for i in range(5):
            st, js = c.login(password="wrong-password-123")
            self.assertEqual(st, 401, js)
        st, js = c.login()   # correct password, but blocked now
        self.assertEqual(st, 429, js)
        # another IP behind NPM isn't blocked; a spoofed X-Forwarded-For from a non-proxy is ignored
        st, js = Client(self.port).login(headers={"X-Forwarded-For": "192.168.1.100"})
        self.assertEqual(st, 429, "XFF from a non-proxy client must be ignored")
        os.environ["GE_TRUSTED_PROXY"] = "127.0.0.1"
        try:
            st, js = Client(self.port).login(headers={"X-Forwarded-For": "1.2.3.4, 192.168.1.100"})
            self.assertEqual(st, 200, "a different client behind the proxy is not blocked")
            for i in range(5):
                Client(self.port).login(password="nope-nope-nope", headers={"X-Forwarded-For": "192.168.1.101"})
            st, _ = Client(self.port).login(headers={"X-Forwarded-For": "192.168.1.101"})
            self.assertEqual(st, 429)
        finally:
            os.environ["GE_TRUSTED_PROXY"] = "192.168.1.5"

    def test_login_needs_login_token(self):
        c = Client(self.port)
        st, js, _, _ = c.req("POST", "/api/login", {"user": "admin", "password": PASSWORD}, csrf=False)
        self.assertEqual(st, 403)
        st, js = c.login(user="root")
        self.assertEqual(st, 401)
        # one login form: a mistyped password, then the right one, with the same token
        c = Client(self.port)
        token = c.req("GET", "/api/session")[1]["login_csrf"]
        st, _, _, _ = c.req("POST", "/api/login", {"user": "admin", "password": "typo-typo-typo"}, headers={"X-CSRF-Token": token})
        self.assertEqual(st, 401)
        st, _, _, _ = c.req("POST", "/api/login", {"user": "admin", "password": PASSWORD}, headers={"X-CSRF-Token": token})
        self.assertEqual(st, 200)

    def test_csrf(self):
        js = self.links()
        body = {"sha": js["sha"], "widgets": js["widgets"]}
        self.assertEqual(self.c.req("POST", "/api/links", body, csrf=False)[0], 403)
        self.assertEqual(self.c.req("POST", "/api/links", body, headers={"X-CSRF-Token": "forged"})[0], 403)
        self.assertEqual(self.c.req("POST", "/api/links", body, headers={"Origin": "https://evil.example"})[0], 403)
        st, _, _, _ = self.c.req("POST", "/api/links", body, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(st, 200)
        # form posts (no JSON) are refused even with a token
        st, _, _, _ = self.c.req("POST", "/api/icons/delete", None, headers={"Content-Type": "text/plain"})
        self.assertIn(st, (415,))

    def test_session_expiry(self):
        os.environ["GE_SESSION_IDLE"] = "1"
        try:
            self.assertEqual(self.c.req("GET", "/api/links")[0], 200)
            time.sleep(1.5)
            self.assertEqual(self.c.req("GET", "/api/links")[0], 401)
        finally:
            del os.environ["GE_SESSION_IDLE"]

    def test_logout(self):
        self.assertEqual(self.c.req("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.c.req("GET", "/api/links")[0], 401)

    def test_password_change_ends_sessions(self):
        from glance_bookmarks_editor import auth, settings
        old = settings.get("GE_PASSWORD_HASH")
        settings.write_setting("GE_PASSWORD_HASH", auth.hash_password(PASSWORD, 600_000))
        try:
            time.sleep(0.01)
            self.assertEqual(self.c.req("GET", "/api/links")[0], 401)
        finally:
            settings.write_setting("GE_PASSWORD_HASH", old)

    # ------------------------------------------------------------ tiles

    def test_tiles_add_edit_delete_reorder_move(self):
        js = self.links()
        bm, apps = js["widgets"]
        bm["groups"][0]["links"].append({"title": "Immich", "url": "https://photos.example.com/",
                                         "icon": "/assets/icons/gmail.webp", "description": "Photos", "same-tab": False})
        bm["groups"][0]["links"][0]["title"] = "Gmail (edited)"
        del bm["groups"][1]["links"][0]                                   # delete Budget
        bm["groups"][0]["links"].reverse()                                 # reorder
        moved = apps["groups"][0]["links"].pop(0)                          # move Wiki: Apps/Tools -> Apps/Network
        apps["groups"][1]["links"].insert(1, moved)
        apps["groups"].reverse()                                           # reorder groups
        r = self.save_links(js)
        self.assertEqual(r["status"], "saved")
        got = yaml.safe_load(self.read("local/links.yml"))
        self.assertEqual([l["title"] for l in got[0]["groups"][0]["links"]], ["Immich", "Bank", "Gmail (edited)"])
        self.assertEqual(got[0]["groups"][0]["links"][0],
                         {"title": "Immich", "url": "https://photos.example.com/", "icon": "/assets/icons/gmail.webp",
                          "description": "Photos", "same-tab": False})
        self.assertEqual(got[0]["groups"][1]["links"], [])
        self.assertEqual([g["title"] for g in got[1]["groups"]], ["Network & Security", "Tools"])
        self.assertEqual([l["title"] for l in got[1]["groups"][0]["links"]], ["NPM", "Wiki", "Glance Bookmarks"])
        text = self.read("local/links.yml")
        self.assertNotIn("\t", text)
        self.assertIn("\n        - title: Immich\n          url: https://photos.example.com/\n", text)
        self.glance_valid()

    def test_groups_add_rename_delete(self):
        js = self.links()
        g = js["widgets"][1]["groups"]
        g.append({"title": "Family", "same-tab": True, "hide-arrow": True, "links": []})
        g[0]["title"] = g[0]["title"] + " (renamed)"
        self.save_links(js)
        js = self.links()
        names = [x["title"] for x in js["widgets"][1]["groups"]]
        self.assertIn("Family", names)
        self.assertTrue(any(n.endswith("(renamed)") for n in names))
        js["widgets"][1]["groups"] = [x for x in js["widgets"][1]["groups"] if x["title"] != "Family"]
        for x in js["widgets"][1]["groups"]:
            x["title"] = x["title"].replace(" (renamed)", "")
        self.save_links(js)
        self.assertNotIn("Family", self.read("local/links.yml"))
        self.glance_valid()

    def test_sections_add_rename_delete_reorder(self):
        orig = self.links()
        js = self.links()
        ws = js["widgets"]
        ws.append({"type": "bookmarks", "title": "Media", "groups": [
            {"title": "Watch", "links": [{"title": "Jellyfin", "url": "https://tv.example.com/"}]}]})
        ws.append({"type": "bookmarks", "groups": []})          # untitled, no groups: valid in Glance
        ws[0]["title"] = "Web"                                   # rename
        ws.insert(0, ws.pop(2))                                  # reorder: Media first
        r = self.save_links(js)
        self.assertEqual(r["status"], "saved")
        got = yaml.safe_load(self.read("local/links.yml"))
        self.assertEqual([w.get("title") for w in got], ["Media", "Web", "Apps", None])
        self.assertEqual(got[3], {"type": "bookmarks", "groups": []})
        self.glance_valid()
        # delete sections down to one
        js = self.links()
        js["widgets"] = [js["widgets"][0]]
        self.save_links(js)
        self.assertEqual(len(yaml.safe_load(self.read("local/links.yml"))), 1)
        self.glance_valid()
        # zero sections, or a widget that isn't a bookmarks widget, is refused and changes nothing
        before = self.read("local/links.yml")
        for widgets in ([], [{"type": "rss", "title": "x", "groups": []}]):
            js = self.links()
            st, r, _, _ = self.c.req("POST", "/api/links", {"sha": js["sha"], "widgets": widgets})
            self.assertEqual(st, 422, r)
            self.assertEqual(self.read("local/links.yml"), before)
        # put the original sections back for the other tests
        js = self.links()
        js["widgets"] = orig["widgets"]
        self.save_links(js)
        self.assertEqual([w["title"] for w in self.links()["widgets"]], ["Bookmarks", "Apps"])

    def test_untitled_groups_are_kept(self):
        js = self.links()
        g = js["widgets"][0]["groups"][0]
        title = g.pop("title")
        self.save_links(js)
        got = yaml.safe_load(self.read("local/links.yml"))
        self.assertNotIn("title", got[0]["groups"][0])
        self.glance_valid()
        js = self.links()
        js["widgets"][0]["groups"][0] = {"title": title, **js["widgets"][0]["groups"][0]}
        self.save_links(js)

    def test_invalid_tiles_rejected_file_unchanged(self):
        before = self.read("local/links.yml")
        bad = [
            ("url", "javascript:alert(1)"), ("url", "ftp://x/"), ("url", "https://a b/"),
            ("title", ""), ("title", "x\ny"), ("title", "${EXAMPLE_API_KEY}"),
            ("icon", "/assets/icons/missing.webp"), ("icon", "https://evil.example/x.png"),
            ("icon", "/assets/icons/../../.env"), ("same-tab", "yes"),
        ]
        for key, val in bad:
            js = self.links()
            js["widgets"][0]["groups"][0]["links"][0][key] = val
            st, r, _, _ = self.c.req("POST", "/api/links", {"sha": js["sha"], "widgets": js["widgets"]})
            self.assertEqual(st, 422, (key, val, r))
            self.assertEqual(self.read("local/links.yml"), before, (key, val))
        # passes the editor's checks but Glance rejects it -> Glance's message, file unchanged
        js = self.links()
        js["widgets"][0]["hide-header"] = "not-a-bool"
        st, r, _, _ = self.c.req("POST", "/api/links", {"sha": js["sha"], "widgets": js["widgets"]})
        self.assertEqual(st, 422, r)
        self.assertEqual(r["kind"], "invalid")
        self.assertTrue(r["detail"])
        self.assertEqual(self.read("local/links.yml"), before)

    def test_conflict(self):
        js = self.links()
        js2 = self.links()
        js["widgets"][0]["groups"][0]["title"] = "General A"
        self.save_links(js)
        js2["widgets"][0]["groups"][0]["title"] = "General B"
        st, r, _, _ = self.c.req("POST", "/api/links", {"sha": js2["sha"], "widgets": js2["widgets"]})
        self.assertEqual(st, 409)
        self.assertIn("General A", self.read("local/links.yml"))
        js = self.links()
        js["widgets"][0]["groups"][0]["title"] = "General"
        self.save_links(js)

    # ------------------------------------------------------------ countdown

    def cd(self):
        st, js, _, _ = self.c.req("GET", "/api/countdown")
        self.assertEqual(st, 200, js)
        return js

    def save_cd(self, js, birthdays, events, expect=200):
        st, r, _, _ = self.c.req("POST", "/api/countdown", {"sha": js["sha"], "birthdays": birthdays, "events": events})
        self.assertEqual(st, expect, r)
        return r

    def test_countdown_limits_and_format(self):
        js = self.cd()
        self.assertEqual(js["birthdays"], [{"date": "03-15", "name": "Alex"}])
        bds = [{"date": "03-15", "name": "Alex"}, {"date": "02-29", "name": "Leap"}, {"date": "12-31", "name": "Sam"}]
        evs = [{"date": "04-03", "name": "Anniversary", "emoji": "💍"},
               {"date": "2027-07-10", "name": "Vacation", "emoji": "✈️"},
               {"date": "10-31", "name": "Halloween party", "emoji": "🎃"},
               {"date": "2030-01-01", "name": "Big one", "emoji": "🎆"},
               {"date": "06-01", "name": "Five", "emoji": "5️⃣"}]
        self.save_cd(js, bds, evs)
        text = self.read("local/countdown.yml")
        self.assertTrue(text.startswith("# Countdown dates"))
        self.assertIn('birthday-3: "12-31|Sam"\n', text)
        self.assertIn('event-2: "2027-07-10|Vacation|✈️"\n', text)
        self.assertIn('event-5: "06-01|Five|5️⃣"\n', text)
        self.glance_valid()
        js = self.cd()
        self.save_cd(js, bds + [{"date": "01-01", "name": "Four"}], evs, expect=422)
        self.save_cd(js, bds, evs + [{"date": "01-01", "name": "Six", "emoji": "x"}], expect=422)
        before = self.read("local/countdown.yml")
        for b, e in [
            ([{"date": "02-30", "name": "X"}], []),
            ([{"date": "2026-03-15", "name": "X"}], []),          # birthdays have no year
            ([{"date": "03-15", "name": "A|B"}], []),
            ([{"date": "03-15", "name": 'A"B'}], []),
            ([{"date": "03-15", "name": ""}], []),
            ([], [{"date": "04-03", "name": "No emoji", "emoji": ""}]),
            ([], [{"date": "04-03", "name": "Pipe", "emoji": "|"}]),
            ([], [{"date": "2027-13-01", "name": "Bad", "emoji": "x"}]),
            ([], [{"date": "04-03", "name": "${EXAMPLE_API_KEY}", "emoji": "x"}]),
        ]:
            self.save_cd(js, b, e, expect=422)
            self.assertEqual(self.read("local/countdown.yml"), before, (b, e))
        # remove everything: still a valid config
        self.save_cd(js, [], [])
        self.assertEqual(self.cd()["birthdays"], [])
        self.glance_valid()
        js = self.cd()
        self.save_cd(js, [{"date": "03-15", "name": "Alex"}],
                     [{"date": "04-03", "name": "Anniversary", "emoji": "💍"}, {"date": "2027-07-10", "name": "Vacation", "emoji": "✈️"}])

    # ------------------------------------------------------------ history

    def test_history_and_restore(self):
        js = self.links()
        js["widgets"][0]["groups"][0]["title"] = "History test"
        self.save_links(js)
        st, h, _, _ = self.c.req("GET", "/api/history")
        self.assertEqual(st, 200)
        newest = h["links"][0]
        st, v, _, _ = self.c.req("GET", f"/api/history/view?file=links&name={newest['name']}")
        self.assertIn("-    - title: History test", v["diff"])
        st, r, _, _ = self.c.req("POST", "/api/history/restore", {"file": "links", "name": newest["name"]})
        self.assertEqual(st, 200, r)
        self.assertNotIn("History test", self.read("local/links.yml"))
        st, h2, _, _ = self.c.req("GET", "/api/history")
        self.assertIn("History test", open(os.path.join(self.data, "local/.history", h2["links"][0]["name"])).read())
        # an old version that uses a since-deleted icon is flagged on view and restore
        js = self.links()
        js["widgets"][0]["groups"][0]["links"][0]["icon"] = "/assets/icons/outline.webp"
        os.makedirs(os.path.join(self.data, "assets/icons"), exist_ok=True)
        open(os.path.join(self.data, "assets/icons/temp-gone.webp"), "wb").write(webp_bytes())
        js["widgets"][0]["groups"][0]["links"][0]["icon"] = "/assets/icons/temp-gone.webp"
        self.save_links(js)
        js = self.links()
        js["widgets"][0]["groups"][0]["links"][0]["icon"] = "/assets/icons/gmail.webp"
        self.save_links(js)
        os.unlink(os.path.join(self.data, "assets/icons/temp-gone.webp"))
        name = self.c.req("GET", "/api/history")[1]["links"][0]["name"]
        self.assertEqual(self.c.req("GET", f"/api/history/view?file=links&name={name}")[1]["missing_icons"], ["temp-gone.webp"])
        st, r, _, _ = self.c.req("POST", "/api/history/restore", {"file": "links", "name": name})
        self.assertEqual((st, r["missing_icons"]), (200, ["temp-gone.webp"]))
        js = self.links()
        js["widgets"][0]["groups"][0]["links"][0]["icon"] = "/assets/icons/gmail.webp"
        self.save_links(js)
        for bad in ("../glance.yml", "links.yml.20260101-000000/../../x", "countdown.yml.20260101-000000"):
            st, _, _, _ = self.c.req("POST", "/api/history/restore", {"file": "links", "name": bad})
            self.assertEqual(st, 404, bad)
        self.glance_valid()

    def test_history_keeps_30(self):
        from glance_bookmarks_editor import pipeline
        for i in range(33):
            pipeline.add_history("local/countdown.yml", f"# {i}\n".encode())
        self.assertEqual(len(pipeline.list_history("local/countdown.yml")), 30)

    # ------------------------------------------------------------ icons

    def test_icon_download_rules(self):
        icons_dir = os.path.join(self.data, "assets", "icons")
        st, r, _, _ = self.c.req("POST", "/api/icons/download", {"name": "immich"})
        self.assertEqual((st, r["status"]), (200, "downloaded"), r)
        self.assertTrue(open(os.path.join(icons_dir, "immich.webp"), "rb").read().startswith(b"RIFF"))
        st, r, _, _ = self.c.req("POST", "/api/icons/download", {"name": "immich"})
        self.assertEqual(r["status"], "exists")
        st, r, _, _ = self.c.req("POST", "/api/icons/download", {"name": "immich", "force": True})
        self.assertEqual(r["status"], "downloaded")
        st, r, _, _ = self.c.req("POST", "/api/icons/download", {"name": "immich-light"})
        self.assertEqual(r["status"], "downloaded")
        for name, why in [("does-not-exist", "no icon"), ("Bad_Name", "lower-case"), ("../etc/passwd", "lower-case"),
                          ("huge", "too large"), ("png-in-disguise", "isn't a WebP"), ("redirect", "")]:
            st, r, _, _ = self.c.req("POST", "/api/icons/download", {"name": name})
            self.assertEqual(st, 422, (name, r))
            self.assertIn(why, r["error"], name)
            self.assertFalse(os.path.exists(os.path.join(icons_dir, f"{name}.webp")), name)
        st, r, _, _ = self.c.req("POST", "/api/icons/download", {"name": "x", "url": "https://evil.example/x.webp"})
        self.assertEqual(st, 422)   # 'x' isn't on the fake server; the url field is ignored

    def test_icon_index(self):
        st, r, _, _ = self.c.req("GET", "/api/icons/index")
        self.assertEqual(st, 200, r)
        self.assertIn(["immich", "Immich", True], r["items"])
        self.assertFalse(any(i[0] == "../etc" for i in r["items"]))

    def test_icon_library_and_delete(self):
        st, r, _, _ = self.c.req("GET", "/api/icons/library")
        items = {i["file"]: i for i in r["items"]}
        self.assertEqual(len(items["gmail.webp"]["used_by"]), 1)
        self.assertEqual(items["bank.svg"]["selfhst"], False)
        st, r, _, _ = self.c.req("POST", "/api/icons/delete", {"file": "gmail.webp"})
        self.assertEqual(st, 422)
        self.assertIn("used by", r["error"])
        self.assertTrue(os.path.exists(os.path.join(self.data, "assets/icons/gmail.webp")))
        for bad in ("../glance.yml", "../../.env", ".env", "nope.webp"):
            st, r, _, _ = self.c.req("POST", "/api/icons/delete", {"file": bad})
            self.assertEqual(st, 422, bad)
        self.assertTrue(os.path.exists(os.path.join(self.data, "glance.yml")))
        open(os.path.join(self.data, "assets/icons/unused.webp"), "wb").write(webp_bytes())
        st, r, _, _ = self.c.req("POST", "/api/icons/delete", {"file": "unused.webp"})
        self.assertEqual(st, 200, r)
        self.assertFalse(os.path.exists(os.path.join(self.data, "assets/icons/unused.webp")))

    def test_icon_serving(self):
        st, _, h, raw = self.c.req("GET", "/icons/bank.svg")
        self.assertEqual(st, 200)
        self.assertIn("sandbox", h["content-security-policy"])
        st, _, h, raw = self.c.req("GET", "/icons/gmail.webp")
        self.assertEqual((st, h["content-type"]), (200, "image/webp"))
        self.assertEqual(self.c.req("GET", "/icons/..%2Fglance.yml")[0], 404)

    # ------------------------------------------------------------ secrets

    def test_secrets_never_returned(self):
        from glance_bookmarks_editor import pipeline
        self.assertEqual(pipeline.redact(f"bad value {SECRET} here", {"K": SECRET}), "bad value *** here")
        for path in ("/api/links", "/api/countdown", "/api/history", "/api/icons/library"):
            raw = self.c.req("GET", path)[3]
            self.assertNotIn(SECRET.encode(), raw, path)
        # a save Glance rejects returns Glance's message, never the .env values it validated with
        js = self.links()
        js["widgets"][0]["hide-header"] = "not-a-bool"
        st, r, _, raw = self.c.req("POST", "/api/links", {"sha": js["sha"], "widgets": js["widgets"]})
        self.assertEqual(st, 422, r)
        self.assertNotIn(SECRET.encode(), raw)

    def test_zz_nothing_written_outside_local_and_icons(self):
        self.assertEqual(self.snapshot_outside(), self.outside)


HOME_VARIANTS = {
    # the Proxmox VE helper script's default page: one untitled bookmarks widget
    "helper": """\
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
                - title: Glance Bookmarks
                  url: http://192.168.1.10:5000/
""",
    # three custom-named widgets in one column, one more in another column
    "many": """\
- name: Home
  columns:
    - size: small
      widgets:
        - type: bookmarks
          title: Elsewhere
          groups:
            - links:
                - title: Docs
                  url: https://docs.example.com/
    - size: full
      widgets:
        - type: search
        - type: bookmarks
          title: Work
          groups:
            - title: Tools
              links:
                - title: Mail
                  url: https://mail.example.com/

        # media apps
        - type: bookmarks
          title: Media
          groups:
            - title: Watch
              links:
                - title: Jellyfin
                  url: https://tv.example.com/
        - type: "bookmarks"
          title: Family
          groups: []
        - type: calendar
""",
    "none": """\
- name: Home
  columns:
    - size: full
      widgets:
        - type: search
""",
}


class MigrationVariantsTest(unittest.TestCase):
    """migrate.plan/apply on Home pages that aren't shaped like the main fixture."""

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("GE_DATA", "GE_GLANCE_BIN", "GE_PUBLIC_URL")}
        self.tmp = tempfile.mkdtemp(prefix="ge-mig-")
        self.data = os.path.join(self.tmp, "glance_data")
        shutil.copytree(os.path.join(HERE, "fixture", "glance_data"), self.data)
        with open(os.path.join(self.data, "glance.yml")) as f:
            g = f.read().replace("/opt/glance_data/assets", os.path.join(self.data, "assets"))
        g = g.replace("  - $include: pages/news.yml\n", "")
        with open(os.path.join(self.data, "glance.yml"), "w") as f:
            f.write(g)
        os.environ.update({"GE_DATA": self.data, "GE_GLANCE_BIN": GLANCE, "GE_PUBLIC_URL": ""})

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use(self, name):
        with open(os.path.join(self.data, "pages", "home.yml"), "w") as f:
            f.write(HOME_VARIANTS[name])

    def test_helper_default_page(self):
        from glance_bookmarks_editor import migrate, model, pipeline
        self.use("helper")
        p = migrate.plan("https://glance-bookmarks.example.com/")
        self.assertEqual(p["moved"], ["(untitled)"])
        self.assertEqual(p["left"], 0)
        self.assertEqual(p["tile_old_url"], "http://192.168.1.10:5000/")
        self.assertIn("        - $include: ../local/links.yml\n", p["home_new"])
        self.assertNotIn("type: bookmarks", p["home_new"])
        migrate.apply(p)
        cur = model.load_links()
        self.assertEqual(len(cur["widgets"]), 1)
        self.assertNotIn("title", cur["widgets"][0])
        # the editor's layout round-trips and validates
        text = model.render_links(model.normalize_links(cur["widgets"], cur["widgets"]))
        ok, msg = pipeline.validate_config({model.LINKS: text.encode()})
        self.assertTrue(ok, msg)

    def test_many_widgets_one_place_moved_others_left(self):
        from glance_bookmarks_editor import migrate, model
        self.use("many")
        p = migrate.plan()
        self.assertEqual(p["moved"], ["Work", "Media", "Family"])
        self.assertEqual(p["left"], 1)
        self.assertIn("title: Elsewhere", p["home_new"])                 # the other column is untouched
        self.assertIn("- type: calendar", p["home_new"])
        self.assertIn("# media apps\n- type: bookmarks\n  title: Media", p["links_new"])
        migrate.apply(p)
        self.assertEqual([w["title"] for w in model.load_links()["widgets"]], ["Work", "Media", "Family"])

    def test_no_bookmarks_widget(self):
        from glance_bookmarks_editor import migrate
        self.use("none")
        with self.assertRaises(migrate.MigrationError) as cm:
            migrate.plan()
        self.assertIn("no `- type: bookmarks` widget", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
