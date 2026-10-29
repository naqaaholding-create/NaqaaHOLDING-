"""pg_compat — drop-in replacement for Python's sqlite3 module backed by PostgreSQL.

The NAQAA Market application was written against the sqlite3 API.  This module
lets the same code run on PostgreSQL (e.g. Supabase) by exposing a
sqlite3-compatible ``connect()`` whose cursors transparently:

* translate ``?`` placeholders to ``%s`` (outside quoted literals),
* translate ``INSERT OR IGNORE`` to ``ON CONFLICT DO NOTHING``,
* translate ``BEGIN IMMEDIATE`` to a transaction-scoped advisory lock,
* translate ``PRAGMA table_info(t)`` to an information_schema query,
* answer ``SELECT changes()`` from the previous statement's rowcount,
* wrap every statement in a SAVEPOINT so a caught statement error does not
  poison the surrounding PostgreSQL transaction (SQLite semantics),
* auto-commit DDL immediately (schema locks must not queue behind the
  advisory lock or long transactions),
* use ``conn.commit()``/``conn.rollback()`` — never ``cursor.execute("COMMIT")``,
  which corrupts psycopg2's implicit-transaction state and silently drops
  advisory locks,
* force UTF-8 client encoding so Arabic text round-trips regardless of the
  server's default encoding.

Isolation model
---------------
Every distinct ``DATABASE_PATH`` (plus the current process id) maps to its own
PostgreSQL **schema** inside the database named by ``DATABASE_URL``.  This
mirrors SQLite's file-per-database semantics — every pytest process and every
``make_client()`` tempdir gets isolated tables — and works on hosted Postgres
(Supabase) where ``CREATE DATABASE`` is not permitted.  Schemas created by a
process are dropped again at process exit (best effort).

Mode selection
--------------
* ``DATABASE_URL`` set (and psycopg2 importable) -> PostgreSQL mode.
* otherwise -> the module re-exports the real ``sqlite3`` unchanged so local
  development and the SQLite test suite keep working exactly as before.
  ``app.py`` enforces that production sets ``DATABASE_URL``.
"""

import hashlib
import os
import re
import sys
import threading

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

try:
    import psycopg2
    import psycopg2.extensions as _pg_ext
    _PG_OK = True
except ImportError:  # pragma: no cover - SQLite-only environment
    psycopg2 = None
    _PG_OK = False

PG_MODE = bool(_DATABASE_URL) and _PG_OK

if not PG_MODE:
    # ------------------------------------------------------------------
    # SQLite fallback: behave exactly like the real module.
    # ------------------------------------------------------------------
    import sqlite3 as _real_sqlite3
    from sqlite3 import *          # noqa: F401,F403 - re-export the whole API
    from sqlite3 import connect as _real_connect

    def connect(path, *args, **kwargs):
        return _real_connect(path, *args, **kwargs)

    def is_pg_mode():
        return False

else:
    # ------------------------------------------------------------------
    # PostgreSQL mode
    # ------------------------------------------------------------------
    import sqlite3 as _real_sqlite3

    # Exception classes that satisfy BOTH sqlite3-style and psycopg2-style
    # except clauses (tests import the real sqlite3, the app imports this).
    class Error(_real_sqlite3.Error, psycopg2.Error):
        pass

    class DatabaseError(_real_sqlite3.DatabaseError, psycopg2.DatabaseError):
        pass

    class OperationalError(_real_sqlite3.OperationalError,
                           psycopg2.OperationalError):
        pass

    class IntegrityError(_real_sqlite3.IntegrityError,
                          psycopg2.IntegrityError):
        pass

    class DataError(_real_sqlite3.DataError, psycopg2.DataError):
        pass

    class ProgrammingError(_real_sqlite3.ProgrammingError,
                           psycopg2.ProgrammingError):
        pass

    _SAVEPOINT_SEQ = [0]
    _SAVEPOINT_LOCK = threading.Lock()
    _MY_SCHEMAS = set()

    def _map_exception(exc):
        """Translate psycopg2 exceptions to the dual sqlite3/psycopg2 classes.

        sqlite3.IntegrityError is raised only for constraint violations
        (PG SQLSTATE class 23); nearly everything else sqlite3 reports as
        OperationalError, including duplicate columns and syntax errors.
        """
        code = getattr(exc, "pgcode", None) or ""
        if code.startswith("23"):
            return IntegrityError(str(exc).strip())
        if isinstance(exc, psycopg2.IntegrityError):
            return IntegrityError(str(exc).strip())
        if isinstance(exc, psycopg2.Error):
            return OperationalError(str(exc).strip())
        return exc

    def _schema_for(path):
        """One PostgreSQL schema per DATABASE_PATH (plus pid under pytest).

        Including the pid only under tests keeps production data in a stable
        schema across restarts, while every pytest process and every
        make_client() tempdir still gets fully isolated tables.
        """
        key = os.path.abspath(str(path))
        if "pytest" in sys.modules:
            key = "{}:{}".format(os.getpid(), key)
        return "naqaa_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    def _dsn_with_options(timeout_seconds=30):
        # Session-level settings (SET inside a tx would be rolled back).
        return _DATABASE_URL, {
            "options": "-c client_encoding=UTF8 -c lock_timeout=%dms"
                       % int(timeout_seconds * 1000),
            "connect_timeout": 10,
        }

    import atexit

    def _cleanup_schemas():  # pragma: no cover - process exit
        if not _MY_SCHEMAS:
            return
        dsn, opts = _dsn_with_options()
        try:
            conn = psycopg2.connect(dsn, **opts)
            conn.autocommit = True
            cur = conn.cursor()
            for schema in _MY_SCHEMAS:
                cur.execute('DROP SCHEMA IF EXISTS "{}" CASCADE'.format(schema))
            conn.close()
        except Exception:
            pass

    atexit.register(_cleanup_schemas)

    class Row(object):
        """sqlite3.Row-compatible row: index and case-insensitive key access."""

        __slots__ = ("_data", "_names", "_index")

        def __init__(self, description, data):
            self._names = [d[0] for d in (description or ())]
            self._index = {}
            for i, name in enumerate(self._names):
                self._index[name] = i
                self._index[name.lower()] = i
            self._data = tuple(data)

        def __len__(self):
            return len(self._data)

        def __getitem__(self, key):
            if isinstance(key, int):
                return self._data[key]
            idx = self._index.get(key)
            if idx is None:
                raise IndexError("no such column: %s" % key)
            return self._data[idx]

        def keys(self):
            return list(self._names)

        def __iter__(self):
            return iter(self._data)

        def __eq__(self, other):
            if isinstance(other, tuple):
                return self._data == other
            if isinstance(other, Row):
                return self._data == other._data
            return NotImplemented

        def __repr__(self):
            return repr(self._data)

    class Cursor(object):
        """sqlite3-compatible cursor over a psycopg2 connection."""

        _DDL_RE = re.compile(
            r"^\s*(CREATE|ALTER|DROP|TRUNCATE|COMMENT|GRANT|REVOKE)\b", re.I)
        _PRAGMA_TABLE_RE = re.compile(
            r"^\s*pragma\s+table_info\(\s*[\"'`]?([\w]+)[\"'`]?\s*\)\s*$", re.I)
        _CHANGES_RE = re.compile(r"^\s*select\s+changes\(\s*\)\s*$", re.I)
        _BEGIN_IMMEDIATE_RE = re.compile(r"^\s*begin\s+(immediate|exclusive)\s*$", re.I)
        _BEGIN_RE = re.compile(r"^\s*begin\s*$", re.I)
        _INSERT_OR_IGNORE_RE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
        _DATETIME_RE = re.compile(r"\bdatetime\(([^()]+)\)", re.I)

        def __init__(self, conn):
            self._conn = conn
            self._ctl = conn._raw.cursor()   # control cursor (SAVEPOINTs, locks)
            self._data = conn._raw.cursor()  # data cursor (results live here)
            self.row_factory = conn.row_factory
            self.description = None
            self.rowcount = -1
            self.arraysize = 1
            self._synthetic = None  # list of tuples for emulated statements

        # -- SQL translation --------------------------------------------
        @staticmethod
        def _replace_placeholders(sql):
            """Replace ? with %s outside single/double-quoted literals."""
            out, lit = [], None
            i, n = 0, len(sql)
            while i < n:
                ch = sql[i]
                if lit:
                    out.append(ch)
                    if ch == lit:
                        if i + 1 < n and sql[i + 1] == lit:  # escaped quote
                            out.append(lit)
                            i += 1
                        else:
                            lit = None
                elif ch in ("'", '"'):
                    lit = ch
                    out.append(ch)
                elif ch == "-" and sql.startswith("--", i):
                    j = sql.find("\n", i)
                    j = n if j == -1 else j
                    out.append(sql[i:j])
                    i = j - 1
                elif ch == "?":
                    out.append("%s")
                else:
                    out.append(ch)
                i += 1
            return "".join(out)

        def _translate(self, sql):
            s = self._replace_placeholders(sql)
            if self._INSERT_OR_IGNORE_RE.match(s):
                s = self._INSERT_OR_IGNORE_RE.sub("INSERT INTO", s, count=1)
                s = s.rstrip()
                if s.endswith(";"):
                    s = s[:-1].rstrip()
                s = s + " ON CONFLICT DO NOTHING"
            s = self._DATETIME_RE.sub(r"(\1)::timestamptz", s)
            return s

        def _next_savepoint(self):
            with _SAVEPOINT_LOCK:
                _SAVEPOINT_SEQ[0] += 1
                return "pgcompat_sp_%d" % _SAVEPOINT_SEQ[0]

        # -- execute -----------------------------------------------------
        def execute(self, sql, params=()):
            sql = sql or ""
            stripped = sql.strip()

            # PRAGMA table_info(t) -> information_schema
            m = self._PRAGMA_TABLE_RE.match(stripped)
            if m:
                table = m.group(1)
                self._ctl.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s AND table_schema = ANY "
                    "(current_schemas(false)) ORDER BY ordinal_position",
                    (table,))
                cols = [r[0] for r in self._ctl.fetchall()]
                # PRAGMA rows: (cid, name, type, notnull, dflt_value, pk)
                self._synthetic = [(i, c, "", 0, None, 0) for i, c in enumerate(cols)]
                self._finish_synthetic()
                return self

            # Other PRAGMAs (busy_timeout, foreign_keys, ...) are no-ops.
            if stripped.lower().startswith("pragma"):
                self._synthetic = []
                self._finish_synthetic()
                return self

            # BEGIN IMMEDIATE -> transactional advisory lock.
            if self._BEGIN_IMMEDIATE_RE.match(stripped):
                key = hashlib.md5(
                    ("begin:" + self._conn._db_key).encode("utf-8")).hexdigest()[:8]
                # Executing the lock opens the implicit transaction; the lock
                # is held until commit()/rollback(), mirroring SQLite's
                # BEGIN IMMEDIATE write lock.
                self._ctl.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s) & 2147483647, %s)",
                    (key, 1))
                self.description = None
                self.rowcount = -1
                self._synthetic = None
                return self

            if self._BEGIN_RE.match(stripped):
                # Plain BEGIN: the next statement opens the transaction.
                self.description = None
                self.rowcount = -1
                self._synthetic = None
                return self

            # SELECT changes() -> previous rowcount.
            if self._CHANGES_RE.match(stripped):
                self._synthetic = [(self._conn._last_rowcount,)]
                self._finish_synthetic()
                return self

            translated = self._translate(sql)
            is_ddl = bool(self._DDL_RE.match(translated))
            sp = self._next_savepoint()
            self._ctl.execute("SAVEPOINT " + sp)
            try:
                if params and not isinstance(params, (tuple, list)):
                    params = tuple(params)
                self._data.execute(translated, params)
            except Exception as exc:
                try:
                    self._ctl.execute("ROLLBACK TO SAVEPOINT " + sp)
                    self._ctl.execute("RELEASE SAVEPOINT " + sp)
                except psycopg2.Error:
                    self._conn._raw.rollback()
                if is_ddl:
                    try:
                        self._conn._raw.commit()
                    except psycopg2.Error:
                        pass
                raise _map_exception(exc)
            else:
                self._ctl.execute("RELEASE SAVEPOINT " + sp)
                if is_ddl:
                    # Auto-commit DDL: schema locks must not queue behind the
                    # advisory lock or behind long transactions.
                    self._conn._raw.commit()
                self.description = self._data.description
                self._synthetic = None
                try:
                    self.rowcount = self._data.rowcount
                except Exception:
                    self.rowcount = -1
                low = stripped.lower()
                if low.startswith(("update", "delete", "insert")):
                    self._conn._last_rowcount = max(self.rowcount, 0)
                return self

        def _finish_synthetic(self):
            self.description = [("value", None, None, None, None, None, None)] \
                if self._synthetic else None
            self.rowcount = len(self._synthetic)

        def executemany(self, sql, seq):
            for params in seq:
                self.execute(sql, params)
            return self

        def executescript(self, script):
            """Commit pending work, run each statement, commit (sqlite3 rules)."""
            self._conn._raw.commit()
            for stmt in self._split_script(script):
                if not stmt.strip():
                    continue
                try:
                    self.execute(stmt)
                except Exception as exc:
                    self._conn._raw.rollback()
                    raise _map_exception(exc)
            self._conn._raw.commit()
            return self

        @staticmethod
        def _split_script(script):
            stmts, buf, lit = [], [], None
            i, n = 0, len(script)
            while i < n:
                ch = script[i]
                if lit:
                    buf.append(ch)
                    if ch == lit:
                        if i + 1 < n and script[i + 1] == lit:
                            buf.append(lit)
                            i += 1
                        else:
                            lit = None
                elif ch in ("'", '"'):
                    lit = ch
                    buf.append(ch)
                elif script.startswith("--", i):
                    j = script.find("\n", i)
                    j = n if j == -1 else j
                    buf.append(script[i:j])
                    i = j - 1
                elif ch == ";":
                    stmts.append("".join(buf))
                    buf = []
                else:
                    buf.append(ch)
                i += 1
            if "".join(buf).strip():
                stmts.append("".join(buf))
            return stmts

        # -- results ------------------------------------------------------
        def _make_row(self, values):
            if self.row_factory is not None:
                try:
                    return self.row_factory(self.description, values)
                except Exception:
                    return tuple(values)
            return tuple(values)

        def fetchone(self):
            if self._synthetic is not None:
                if not self._synthetic:
                    return None
                return self._synthetic.pop(0)
            try:
                row = self._data.fetchone()
            except psycopg2.ProgrammingError:
                return None
            if row is None:
                return None
            return self._make_row(row)

        def fetchall(self):
            if self._synthetic is not None:
                rows, self._synthetic = self._synthetic, []
                return rows
            try:
                rows = self._data.fetchall()
            except psycopg2.ProgrammingError:
                rows = []
            return [self._make_row(r) for r in rows]

        def fetchmany(self, size=None):
            size = size or self.arraysize
            out = []
            for _ in range(size):
                r = self.fetchone()
                if r is None:
                    break
                out.append(r)
            return out

        def __iter__(self):
            while True:
                r = self.fetchone()
                if r is None:
                    return
                yield r

        def close(self):
            for cur in (self._ctl, self._data):
                try:
                    cur.close()
                except Exception:
                    pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

    class Connection(object):
        """sqlite3-compatible connection over psycopg2."""

        def __init__(self, path="naqaa_market.db", timeout=30.0, **kwargs):
            self._db_key = str(path)
            schema = _schema_for(path)
            dsn, opts = _dsn_with_options(timeout if isinstance(timeout, (int, float)) else 30.0)
            self._raw = psycopg2.connect(dsn, **opts)
            self._raw.autocommit = False
            with self._raw.cursor() as c:
                c.execute('CREATE SCHEMA IF NOT EXISTS "{}"'.format(schema))
                c.execute('SET search_path TO "{}", public'.format(schema))
            self._raw.commit()
            _MY_SCHEMAS.add(schema)
            self.row_factory = Row
            self._last_rowcount = 0
            self._closed = False

        # -- sqlite3 Connection API ----------------------------------------
        def cursor(self, factory=None):
            cur = Cursor(self)
            if factory is not None:
                cur.row_factory = factory
            return cur

        def execute(self, sql, params=()):
            return self.cursor().execute(sql, params)

        def executescript(self, script):
            return self.cursor().executescript(script)

        def executemany(self, sql, seq):
            return self.cursor().executemany(sql, seq)

        def commit(self):
            self._raw.commit()

        def rollback(self):
            self._raw.rollback()

        def close(self):
            if self._closed:
                return
            self._closed = True
            try:
                self._raw.rollback()
            except Exception:
                pass
            self._raw.close()

        def create_function(self, *a, **k):
            pass  # SQLite-only; PostgreSQL mode ignores custom functions.

        def interrupt(self):
            self._raw.cancel()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
            self.close()

    def connect(path="naqaa_market.db", *args, **kwargs):
        return Connection(path, **kwargs)

    def is_pg_mode():
        return True
