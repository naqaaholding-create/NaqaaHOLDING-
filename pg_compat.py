"""SQLite-compatible data-access layer backed by PostgreSQL (psycopg2).

Activates automatically when DATABASE_URL is set. Without DATABASE_URL the
application keeps using SQLite; production startup requires DATABASE_URL unless
NAQAA_ALLOW_SQLITE=1 is explicitly set for local development.
"""
import hashlib
import os
import re
import threading

try:
    import psycopg2
    from psycopg2 import pool as _pg_pool
except ImportError:  # pragma: no cover - SQLite-only environments
    psycopg2 = None
    _pg_pool = None

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
ALLOW_SQLITE = os.environ.get("NAQAA_ALLOW_SQLITE", "0") == "1"
PG_MODE = bool(DATABASE_URL)

if PG_MODE and psycopg2 is None:
    raise RuntimeError("DATABASE_URL is set but psycopg2 is not installed")

_pool_lock = threading.Lock()
_pools = {}


def _pool_for(url):
    with _pool_lock:
        pool = _pools.get(url)
        if pool is None:
            pool = _pg_pool.SimpleConnectionPool(1, 10, dsn=url)
            _pools[url] = pool
        return pool


def drain_pool():
    """Close all pooled connections (used by tests and graceful shutdown)."""
    with _pool_lock:
        for pool in _pools.values():
            try:
                pool.closeall()
            except Exception:
                pass
        _pools.clear()


def _lock_key(dbname):
    return int(hashlib.sha256(dbname.encode("utf-8")).hexdigest()[:15], 16)


_DATETIME_RE = re.compile(r"datetime\(([^()]*)\)", re.I)


def _translate(sql):
    s = sql.strip().rstrip(";")
    if not s:
        return s
    upper = s.upper()
    if upper.startswith("PRAGMA"):
        return None
    if upper.startswith("INSERT OR IGNORE INTO"):
        s = "INSERT INTO " + s[len("INSERT OR IGNORE INTO"):]
        if "ON CONFLICT" not in upper:
            s += " ON CONFLICT DO NOTHING"
    if upper.startswith("INSERT OR REPLACE INTO"):
        s = "INSERT INTO " + s[len("INSERT OR REPLACE INTO"):]
        if "ON CONFLICT" not in upper:
            s += " ON CONFLICT DO UPDATE SET id=EXCLUDED.id"

    def _dt(m):
        inner = m.group(1).strip()
        if inner.lower() in ("'now'", "now"):
            return "now()"
        return "(%s)::timestamp" % inner

    s = _DATETIME_RE.sub(_dt, s)
    s = re.sub(r"\bSELECT\s+changes\(\)", "SELECT %s", s, flags=re.I)
    return s


class Row:
    """Lightweight sqlite3.Row replacement (index and key access)."""

    def __init__(self, columns, values):
        self._columns = tuple(columns)
        self._values = tuple(values)

    def keys(self):
        return self._columns

    def __len__(self):
        return len(self._values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._columns.index(key)]
        except ValueError:
            raise KeyError(key)

    def __iter__(self):
        return iter(self._values)

    def __repr__(self):
        return "Row(%s)" % (dict(zip(self._columns, self._values)),)


class PGCursor:
    def __init__(self, conn):
        self._conn = conn
        self._cur = conn._raw.cursor()
        self._rows = None
        self._index = 0
        self.description = None

    def _load(self):
        if self.description is None and self._cur.description:
            self.description = [d[0] for d in self._cur.description]
            self._rows = [Row(self.description, r) for r in self._cur.fetchall()]
            self._index = 0

    def execute(self, sql, params=()):
        translated = _translate(sql)
        if translated is None:
            self._rows = []
            self.description = []
            return self
        if "%s" in translated and translated.count("%s") == 1 and "%s" not in sql:
            translated = translated % (self._conn.last_rowcount,)
        self._cur.execute(translated, params)
        self._conn.last_rowcount = self._cur.rowcount
        self._rows = None
        self.description = None
        self._load()
        return self

    def fetchone(self):
        self._load()
        if not self._rows or self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        self._load()
        rows = self._rows or []
        self._index = len(rows)
        return rows

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class PGConnection:
    """sqlite3-like connection wrapper over a pooled psycopg2 connection."""

    def __init__(self, raw, dbname):
        self._raw = raw
        self._dbname = dbname
        self._lock_key = _lock_key(dbname)
        self._in_tx = False
        self._sp_counter = 0
        self.last_rowcount = 0
        raw.set_client_encoding("UTF8")
        cur = raw.cursor()
        cur.execute("SET lock_timeout = '10s'")
        cur.close()

    def cursor(self):
        return PGCursor(self)

    def _is_tx_control(self, sql):
        u = sql.strip().upper()
        return u.startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"))

    def _is_ddl(self, sql):
        u = sql.strip().upper()
        return u.startswith(("CREATE", "ALTER", "DROP", "REINDEX"))

    def execute(self, sql, params=()):
        cur = self.cursor()
        try:
            return cur.execute(sql, params)
        finally:
            pass

    def executescript(self, script):
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in statements:
            translated = _translate(stmt)
            if translated is None:
                continue
            cur = self._raw.cursor()
            cur.execute(translated)
            cur.close()
        self._raw.commit()

    def commit(self):
        # IMPORTANT: use connection-level commit. cursor.execute("COMMIT")
        # leaves psycopg2 in a state where subsequent implicit transactions
        # (and therefore session advisory locks) do not behave correctly.
        self._raw.commit()
        self._in_tx = False

    def rollback(self):
        self._raw.rollback()
        self._in_tx = False

    def close(self):
        try:
            self._raw.rollback()
        except Exception:
            pass
        pool = _pools.get(DATABASE_URL)
        if pool is not None:
            try:
                pool.putconn(self._raw)
            except Exception:
                try:
                    self._raw.close()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def connect(db_path=None):
    """Return a sqlite3-like connection.

    PostgreSQL mode when DATABASE_URL is set; otherwise real sqlite3.
    """
    if not PG_MODE:
        import sqlite3
        return sqlite3.connect(db_path or "naqaa_market.db", timeout=30.0)
    pool = _pool_for(DATABASE_URL)
    raw = pool.getconn()
    dbname = DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
    return PGConnection(raw, dbname)
