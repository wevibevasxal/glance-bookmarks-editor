"""WSGI app + threaded server (standard library only)."""

import difflib
import hmac
import html
import http.cookies
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import socketserver
import sys
import threading
import time
import urllib.parse
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import __version__, auth, icons, model, pipeline, settings

log = logging.getLogger("glance-bookmarks-editor")
STATIC = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "static"))
MAX_BODY = 1024 * 1024

CSP = ("default-src 'self'; img-src 'self' https://cdn.jsdelivr.net https://raw.githubusercontent.com; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = [
    ("Content-Security-Policy", CSP),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
]

SESSIONS = auth.Sessions()
LIMITER = auth.RateLimiter()

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")


class HTTPError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


STATUS = {200: "200 OK", 304: "304 Not Modified", 400: "400 Bad Request", 401: "401 Unauthorized",
          403: "403 Forbidden", 404: "404 Not Found", 405: "405 Method Not Allowed",
          409: "409 Conflict", 413: "413 Payload Too Large", 415: "415 Unsupported Media Type",
          422: "422 Unprocessable Entity", 429: "429 Too Many Requests", 500: "500 Internal Server Error",
          502: "502 Bad Gateway"}


class Request:
    def __init__(self, environ):
        self.env = environ
        self.method = environ["REQUEST_METHOD"]
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
        self.ip = auth.client_ip(environ)
        c = http.cookies.SimpleCookie()
        try:
            c.load(environ.get("HTTP_COOKIE", ""))
        except http.cookies.CookieError:
            pass
        self.cookies = {k: v.value for k, v in c.items()}
        self.session = None
        self.token = self.cookies.get(auth.COOKIE)
        self.headers_out = []

    def q(self, name):
        return (self.query.get(name) or [""])[0]

    def json(self):
        ctype = self.env.get("CONTENT_TYPE", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HTTPError(415, "expected application/json")
        try:
            n = int(self.env.get("CONTENT_LENGTH") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            raise HTTPError(413, "request too large")
        raw = self.env["wsgi.input"].read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise HTTPError(400, "invalid JSON")
        if not isinstance(body, dict):
            raise HTTPError(400, "expected a JSON object")
        return body


ROUTES = {}


def route(method, path, auth_required=True):
    def deco(fn):
        ROUTES[(method, path)] = (fn, auth_required)
        return fn
    return deco


# ---------------------------------------------------------------- public

@route("GET", "/health", auth_required=False)
def health(req):
    return {"status": "ok"}


@route("GET", "/api/session", auth_required=False)
def session_info(req):
    s = SESSIONS.get(req.token)
    if s:
        return {"user": settings.get("GE_USER"), "csrf": s["csrf"], "version": __version__,
                "idle_timeout": SESSIONS.idle()}
    t = SESSIONS.new_login_token()
    req.headers_out.append(auth.cookie_header(auth.LOGIN_COOKIE, t, max_age=600, path="/api/login"))
    raise HTTPError(401, "login required", login_csrf=t, password_set=bool(auth.password_hash()))


@route("POST", "/api/login", auth_required=False)
def login(req):
    if not SESSIONS.check_login_token(req.cookies.get(auth.LOGIN_COOKIE), req.env.get("HTTP_X_CSRF_TOKEN")):
        raise HTTPError(403, "the login form expired; reload the page")
    wait = LIMITER.blocked(req.ip)
    if wait:
        log.warning("login rate limit hit from %s", req.ip)
        raise HTTPError(429, f"too many failed logins; try again in {wait // 60 + 1} min", retry_after=wait)
    body = req.json()
    stored = auth.password_hash()
    if not stored:
        raise HTTPError(403, "no password set yet: run `glance-bookmarks-editor passwd` in the container")
    user, pw = body.get("user"), body.get("password")
    ok = (isinstance(user, str) and isinstance(pw, str) and len(pw) <= 1024
          and auth.verify_password(pw, stored)
          and hmac.compare_digest(user.encode(), settings.get("GE_USER").encode()))
    if not ok:
        LIMITER.failed(req.ip)
        log.warning("failed login from %s", req.ip)
        raise HTTPError(401, "wrong user name or password")
    LIMITER.reset(req.ip)
    SESSIONS.drop_login_token(req.cookies.get(auth.LOGIN_COOKIE))
    req.headers_out.append(auth.cookie_header(auth.LOGIN_COOKIE, "", max_age=0, path="/api/login"))
    token, csrf = SESSIONS.create()
    req.headers_out.append(auth.cookie_header(auth.COOKIE, token))
    log.info("login from %s", req.ip)
    return {"user": settings.get("GE_USER"), "csrf": csrf, "version": __version__}


@route("POST", "/api/logout")
def logout(req):
    SESSIONS.drop(req.token)
    req.headers_out.append(auth.cookie_header(auth.COOKIE, "", max_age=0))
    return {"status": "logged out"}


# ---------------------------------------------------------------- links / countdown

def _model_call(fn, *a):
    try:
        return fn(*a)
    except model.ModelError as e:
        raise HTTPError(422, str(e))
    except pipeline.PipelineError as e:
        raise HTTPError(409 if e.kind == "conflict" else 422, str(e), detail=e.detail, kind=e.kind)


@route("GET", "/api/links")
def get_links(req):
    return _model_call(model.load_links)


@route("POST", "/api/links")
def post_links(req):
    b = req.json()
    return _model_call(model.save_links, b.get("widgets"), b.get("sha"))


@route("GET", "/api/countdown")
def get_countdown(req):
    return _model_call(model.load_countdown)


@route("POST", "/api/countdown")
def post_countdown(req):
    b = req.json()
    return _model_call(model.save_countdown, b.get("birthdays"), b.get("events"), b.get("sha"))


# ---------------------------------------------------------------- icons

def _icon_call(fn, *a):
    try:
        return fn(*a)
    except icons.IconError as e:
        raise HTTPError(422, str(e))


@route("GET", "/api/icons/index")
def icon_index(req):
    items, stale = _icon_call(icons.index)
    return {"items": items, "stale": stale}


@route("GET", "/api/icons/library")
def icon_library(req):
    return {"items": icons.library()}


@route("POST", "/api/icons/download")
def icon_download(req):
    b = req.json()
    return _icon_call(icons.download, b.get("name"), bool(b.get("force")))


@route("POST", "/api/icons/delete")
def icon_delete(req):
    b = req.json()
    with pipeline.SAVE_LOCK:  # no tile can start using it mid-delete
        return _icon_call(icons.delete, b.get("file"))


def serve_icon(req, start, name):
    if not model._ICON_FILE.match(name) or ".." in name:
        raise HTTPError(404, "not found")
    p = os.path.join(model.icons_dir(), name)
    if os.path.islink(p) or not os.path.isfile(p):
        raise HTTPError(404, "not found")
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not ctype.startswith("image/"):
        raise HTTPError(404, "not found")
    with open(p, "rb") as f:
        data = f.read()
    hdrs = [("Content-Type", ctype), ("Cache-Control", "private, max-age=60")]
    if ctype == "image/svg+xml":
        hdrs.append(("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox"))
    return data, hdrs


# ---------------------------------------------------------------- history

FILES = {"links": model.LINKS, "countdown": model.COUNTDOWN}


def _file(req_value):
    rel = FILES.get(req_value)
    if not rel:
        raise HTTPError(400, "file must be links or countdown")
    return rel


@route("GET", "/api/history")
def history(req):
    return {k: pipeline.list_history(rel) for k, rel in FILES.items()}


def _missing_icons(text):
    """Library icons a file refers to that are no longer in assets/icons (Glance doesn't check this)."""
    out = []
    for m in sorted(set(re.findall(r"/assets/icons/([A-Za-z0-9._-]+)", text))):
        if not os.path.isfile(os.path.join(model.icons_dir(), m)):
            out.append(m)
    return out


@route("GET", "/api/history/view")
def history_view(req):
    rel = _file(req.q("file"))
    try:
        old = pipeline.read_history(rel, req.q("name")).decode("utf-8", "replace")
    except pipeline.PipelineError as e:
        raise HTTPError(404, str(e))
    cur = (pipeline.read(rel)[0] or b"").decode("utf-8", "replace")
    diff = "".join(difflib.unified_diff(cur.splitlines(True), old.splitlines(True),
                                        f"current {rel}", f"{req.q('name')} (would be restored)"))
    return {"content": old, "diff": diff or "(identical to the current file)", "missing_icons": _missing_icons(old)}


@route("POST", "/api/history/restore")
def history_restore(req):
    b = req.json()
    rel = _file(b.get("file"))
    try:
        data = pipeline.read_history(rel, b.get("name"))
    except pipeline.PipelineError as e:
        raise HTTPError(404, str(e))
    log.info("restore %s from %s", rel, b.get("name"))
    result = _model_call(pipeline.save, rel, data, b.get("sha") or pipeline.read(rel)[1])
    result["missing_icons"] = _missing_icons(data.decode("utf-8", "replace"))
    return result


# ---------------------------------------------------------------- static

def serve_static(path):
    rel = "index.html" if path in ("/", "/index.html") else path[len("/static/"):]
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", rel) or ".." in rel:
        raise HTTPError(404, "not found")
    p = os.path.realpath(os.path.join(STATIC, rel))
    if not p.startswith(STATIC + os.sep) or not os.path.isfile(p):
        raise HTTPError(404, "not found")
    with open(p, "rb") as f:
        data = f.read()
    if rel == "index.html":
        data = data.replace(b"{{VERSION}}", __version__.encode())
        gurl = settings.get("GE_GLANCE_URL").strip()
        if not re.fullmatch(r"https?://[^\s\"'<>]+", gurl):
            gurl = ""
        data = data.replace(b"{{GLANCE_URL}}", html.escape(gurl or "#", quote=True).encode())
        data = data.replace(b"{{GLANCE_URL_HIDDEN}}", b"" if gurl else b"hidden")
    ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    return data, [("Content-Type", ctype), ("Cache-Control", "no-cache")]


# ---------------------------------------------------------------- dispatch

def _same_origin(req):
    origin = req.env.get("HTTP_ORIGIN")
    if not origin:
        return True
    host = req.env.get("HTTP_X_FORWARDED_HOST") if req.env.get("REMOTE_ADDR") == settings.get("GE_TRUSTED_PROXY") \
        else None
    host = host or req.env.get("HTTP_HOST", "")
    return urllib.parse.urlsplit(origin).netloc.lower() == host.lower()


def app(environ, start_response):
    req = Request(environ)
    t0 = time.time()
    status, body, headers = 500, b"", []
    try:
        if req.method == "POST" and not _same_origin(req):
            raise HTTPError(403, "cross-origin request refused")
        if req.method == "GET" and req.path == "/favicon.ico":
            body, headers = serve_static("/static/favicon.svg")
            status = 200
        elif req.method == "GET" and (req.path == "/" or req.path.startswith("/static/")):
            body, headers = serve_static(req.path)
            status = 200
        elif req.method == "GET" and req.path.startswith("/icons/"):
            if not SESSIONS.get(req.token):
                raise HTTPError(401, "login required")
            body, headers = serve_icon(req, start_response, req.path[len("/icons/"):])
            status = 200
        else:
            entry = ROUTES.get((req.method, req.path))
            if not entry:
                if any(p == req.path for (_, p) in ROUTES):
                    raise HTTPError(405, "method not allowed")
                raise HTTPError(404, "not found")
            fn, need_auth = entry
            if need_auth:
                req.session = SESSIONS.get(req.token)
                if not req.session:
                    raise HTTPError(401, "login required (your session may have expired)")
                if req.method == "POST":
                    sent = environ.get("HTTP_X_CSRF_TOKEN", "")
                    if not sent or not hmac.compare_digest(sent, req.session["csrf"]):
                        log.warning("CSRF token mismatch from %s on %s", req.ip, req.path)
                        raise HTTPError(403, "invalid CSRF token; reload the page")
            result = fn(req)
            status = 200
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]
    except HTTPError as e:
        status = e.status
        payload = {"error": e.message}
        payload.update({k: v for k, v in e.extra.items() if k != "retry_after"})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]
        if "retry_after" in e.extra:
            headers.append(("Retry-After", str(e.extra["retry_after"])))
    except Exception:  # noqa: BLE001 — never leak a traceback to the client
        log.exception("unhandled error on %s %s", req.method, req.path)
        status = 500
        body = b'{"error":"internal error; see /var/log/glance-bookmarks-editor/glance-bookmarks-editor.log"}'
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]
    sec = SECURITY_HEADERS
    if any(k == "Content-Security-Policy" for k, _ in headers):  # e.g. sandboxed SVG icons
        sec = [h for h in SECURITY_HEADERS if h[0] != "Content-Security-Policy"]
    headers = headers + req.headers_out + sec + [("Content-Length", str(len(body)))]
    start_response(STATUS.get(status, f"{status} Error"), headers)
    if req.path != "/health":
        log.info("%s %s %s %d %.0fms", req.ip, req.method, req.path, status, (time.time() - t0) * 1000)
    return [body]


# ---------------------------------------------------------------- server

class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):  # access logging is done in app()
        pass


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.addHandler(sh)
    d = settings.get("GE_LOG_DIR")
    try:
        os.makedirs(d, mode=0o750, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(os.path.join(d, "glance-bookmarks-editor.log"),
                                                  maxBytes=1_000_000, backupCount=3)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError as e:
        log.warning("file logging disabled: %s", e)


def serve():
    setup_logging()
    host, port = settings.get("GE_LISTEN"), int(settings.get("GE_PORT"))
    if not auth.password_hash():
        log.warning("no password set: run `glance-bookmarks-editor passwd`")
    icons.tmp_cleanup()

    def janitor():
        while True:
            time.sleep(600)
            SESSIONS.purge()
    threading.Thread(target=janitor, daemon=True).start()
    httpd = make_server(host, port, app, server_class=ThreadingWSGIServer, handler_class=QuietHandler)
    log.info("glance-bookmarks-editor %s listening on %s:%d (data %s)", __version__, host, port, settings.data_dir())
    httpd.serve_forever()
