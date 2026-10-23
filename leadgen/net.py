"""HTTP client with an on-disk response cache, retries with backoff and per-host rate limiting."""
import hashlib
import json
import logging
import random
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_BODY_BYTES = 3_000_000


class Response:
    def __init__(self, status, text, url, content_type="", fetched_at=None, from_cache=False):
        self.status = status
        self.text = text
        self.url = url
        self.content_type = content_type
        self.fetched_at = fetched_at or time.time()
        self.from_cache = from_cache

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        return json.loads(self.text)


class ResponseCache:
    """SQLite key/value store of HTTP responses."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                " key TEXT PRIMARY KEY, namespace TEXT, fetched_at REAL,"
                " status INTEGER, url TEXT, content_type TEXT, body TEXT)"
            )
            self._db.commit()

    def get(self, key, ttl_days):
        with self._lock:
            row = self._db.execute(
                "SELECT fetched_at, status, url, content_type, body FROM responses WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        fetched_at, status, url, content_type, body = row
        if ttl_days is not None and time.time() - fetched_at > ttl_days * 86400:
            return None
        return Response(status, body, url, content_type, fetched_at, from_cache=True)

    def put(self, key, namespace, resp):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, namespace, resp.fetched_at, resp.status, resp.url, resp.content_type, resp.text),
            )
            self._db.commit()


def make_key(namespace, method, url, body=None, extra=None):
    raw = json.dumps([namespace, method.upper(), url, body, extra], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class HttpClient:
    def __init__(self, cache, user_agent, timeout=15, retries=3, refresh=False):
        self.cache = cache
        self.timeout = timeout
        self.retries = retries
        self.refresh = refresh          # True = ignore cached copies (but still store new ones)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.network_calls = Counter()  # namespace -> real (uncached) requests
        self._host_last = {}
        self._host_locks = {}
        self._locks_guard = threading.Lock()

    def is_cached(self, key, ttl_days):
        return not self.refresh and self.cache.get(key, ttl_days) is not None

    def request(self, method, url, *, namespace, ttl_days, json_body=None, headers=None,
                cache_key=None, min_interval=0.0, cache_statuses=(200, 404, 410), html_only=False):
        """Send a request, or return a cached copy younger than ttl_days."""
        key = cache_key or make_key(namespace, method, url, json_body)
        if not self.refresh:
            cached = self.cache.get(key, ttl_days)
            if cached is not None:
                log.debug("cache hit %s %s", namespace, url)
                return cached

        resp = self._send(method, url, json_body, headers, min_interval, html_only)
        self.network_calls[namespace] += 1
        if resp.status in cache_statuses:
            self.cache.put(key, namespace, resp)
        return resp

    def _host_lock(self, host):
        with self._locks_guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _send(self, method, url, json_body, headers, min_interval, html_only):
        host = urlsplit(url).netloc
        last_error = None
        for attempt in range(self.retries + 1):
            with self._host_lock(host):
                wait = self._host_last.get(host, 0) + min_interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = self.session.request(method, url, json=json_body, headers=headers,
                                             timeout=self.timeout, stream=True, allow_redirects=True)
                    resp = self._read(r, html_only)
                except requests.RequestException as e:
                    resp, last_error = None, e
                finally:
                    self._host_last[host] = time.time()

            if resp is not None and resp.status not in RETRY_STATUSES:
                return resp
            if attempt == self.retries:
                break
            delay = 2 ** (attempt + 1) + random.uniform(0, 1)
            if resp is not None:
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = min(int(retry_after), 60)
                log.info("HTTP %s from %s, retrying in %.0fs", resp.status, host, delay)
            else:
                log.info("%s for %s, retrying in %.0fs", type(last_error).__name__, host, delay)
            time.sleep(delay)

        if resp is not None:
            return resp
        log.warning("giving up on %s: %s", url, last_error)
        return Response(0, "", url, "")

    @staticmethod
    def _read(r, html_only):
        content_type = r.headers.get("Content-Type", "")
        if html_only and r.ok and "html" not in content_type.lower():
            r.close()
            return Response(r.status_code, "", r.url, content_type)
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                break
        r.close()
        encoding = r.encoding if "charset" in content_type.lower() else "utf-8"
        text = body.decode(encoding or "utf-8", errors="replace")
        return Response(r.status_code, text, r.url, content_type)
