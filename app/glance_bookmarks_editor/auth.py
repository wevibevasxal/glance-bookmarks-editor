"""Single-admin login: PBKDF2-SHA256 hash, in-memory sessions, CSRF tokens, login rate limit."""

import collections
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time

from . import settings

ITERATIONS = 600_000
COOKIE = "ge_session"
LOGIN_COOKIE = "ge_login"
RATE_MAX = 5
RATE_WINDOW = 300
MIN_PASSWORD = 12


# ---------------------------------------------------------------- password hash

def hash_password(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{salt.hex()}:{dk.hex()}"


def verify_password(password, stored):
    try:
        algo, iters, salt, digest = stored.split(":")
        iters = int(iters)
        if algo != "pbkdf2_sha256" or iters < 100_000:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iters)
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


def password_hash():
    return settings.get("GE_PASSWORD_HASH")


# ---------------------------------------------------------------- client address

def client_ip(environ):
    """REMOTE_ADDR, or the last X-Forwarded-For hop when the request came from the trusted reverse proxy (GE_TRUSTED_PROXY)."""
    remote = environ.get("REMOTE_ADDR", "")
    trusted = settings.get("GE_TRUSTED_PROXY")
    if trusted and remote == trusted:
        xff = environ.get("HTTP_X_FORWARDED_FOR", "")
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            try:
                return str(ipaddress.ip_address(hops[-1]))
            except ValueError:
                pass
    return remote


# ---------------------------------------------------------------- rate limit

class RateLimiter:
    def __init__(self, max_tries=RATE_MAX, window=RATE_WINDOW):
        self.max, self.window = max_tries, window
        self.tries = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def _prune(self, q, now):
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, ip):
        """Seconds until the next try is allowed, or 0."""
        with self.lock:
            now = time.time()
            q = self.tries[ip]
            self._prune(q, now)
            if len(q) >= self.max:
                return int(self.window - (now - q[0])) + 1
            return 0

    def failed(self, ip):
        with self.lock:
            self.tries[ip].append(time.time())

    def reset(self, ip):
        with self.lock:
            self.tries.pop(ip, None)


# ---------------------------------------------------------------- sessions

class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}          # sha256(token) -> {csrf, seen, pw}
        self.login_tokens = {}   # pre-login CSRF token -> expiry

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def idle(self):
        return int(settings.get("GE_SESSION_IDLE") or 43200)

    def create(self):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock:
            self.items[self._key(token)] = {"csrf": csrf, "seen": time.time(), "pw": password_hash()}
        return token, csrf

    def get(self, token):
        """Session dict for a valid, unexpired token (touches it), else None.
        Changing the password (glance-bookmarks-editor passwd) ends every session."""
        if not token:
            return None
        now = time.time()
        with self.lock:
            k = self._key(token)
            s = self.items.get(k)
            if not s:
                return None
            if now - s["seen"] > self.idle() or s["pw"] != password_hash():
                del self.items[k]
                return None
            s["seen"] = now
            return s

    def drop(self, token):
        with self.lock:
            self.items.pop(self._key(token or ""), None)

    def new_login_token(self):
        t = secrets.token_urlsafe(24)
        with self.lock:
            now = time.time()
            self.login_tokens = {k: v for k, v in self.login_tokens.items() if v > now}
            if len(self.login_tokens) > 1000:
                self.login_tokens.clear()
            self.login_tokens[t] = now + 600
        return t

    def check_login_token(self, cookie_value, header_value):
        if not cookie_value or not header_value or not hmac.compare_digest(cookie_value, header_value):
            return False
        with self.lock:   # reusable until it expires, so a mistyped password doesn't expire the form
            exp = self.login_tokens.get(cookie_value, 0)
        return exp > time.time()

    def drop_login_token(self, value):
        with self.lock:
            self.login_tokens.pop(value or "", None)

    def purge(self):
        now = time.time()
        with self.lock:
            idle = self.idle()
            for k in [k for k, s in self.items.items() if now - s["seen"] > idle]:
                del self.items[k]


def cookie_header(name, value, max_age=None, path="/"):
    parts = [f"{name}={value}", f"Path={path}", "HttpOnly", "SameSite=Strict"]
    if settings.get("GE_COOKIE_SECURE") != "0":   # 0 only for plain-HTTP setups (not recommended)
        parts.insert(2, "Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return ("Set-Cookie", "; ".join(parts))
