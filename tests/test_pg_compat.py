"""Focused tests for the pg_compat SQLite-to-PostgreSQL adapter.

These run only in PostgreSQL mode (DATABASE_URL set); under the SQLite
fallback they are skipped because they exercise translation behaviour that
only exists in PG mode.  They pin the exact failure modes found during the
PG migration of this codebase:

* ``?`` placeholders must become ``%s`` outside string literals,
* ``INSERT OR IGNORE`` must become ``ON CONFLICT DO NOTHING``,
* a failed statement must not poison the transaction (SAVEPOINTs),
* ``BEGIN IMMEDIATE`` must take a real advisory lock and hold it until
  commit/rollback,
* ``SELECT changes()`` must report the previous statement's rowcount,
* ``PRAGMA table_info`` must return the sqlite3 row shape,
* Arabic text must round-trip (UTF-8 client encoding).
"""

import os
import tempfile

import pytest

import pg_compat

pytestmark = [
    pytest.mark.pg_mode,
    pytest.mark.skipif(not pg_compat.PG_MODE,
                       reason="pg_compat is in SQLite fallback mode"),
]


def _tmp_db_path():
    return os.path.join(tempfile.mkdtemp(), "adapter.db")


def test_placeholder_translation_skips_string_literals():
    cur = pg_compat.Cursor.__new__(pg_compat.Cursor)  # no connection needed
    translated = cur._translate("SELECT * FROM accounts WHERE name='?why?' AND id=?")
    assert translated == "SELECT * FROM accounts WHERE name='?why?' AND id=%s"


def test_insert_or_ignore_becomes_on_conflict():
    cur = pg_compat.Cursor.__new__(pg_compat.Cursor)
    translated = cur._translate(
        "INSERT OR IGNORE INTO pricing_settings(code) VALUES(?)")
    assert translated == "INSERT INTO pricing_settings(code) VALUES(%s) ON CONFLICT DO NOTHING"


def test_failed_statement_does_not_poison_transaction():
    conn = pg_compat.connect(_tmp_db_path())
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS poison_t(id TEXT PRIMARY KEY)")
    conn.commit()
    # Statement 1 fails (duplicate column style error), statement 2 must work.
    with pytest.raises(pg_compat.OperationalError):
        c.execute("SELECT no_such_column FROM poison_t")
    c.execute("SELECT id FROM poison_t")
    assert c.fetchall() == []
    conn.close()


def test_begin_immediate_holds_advisory_lock_until_commit():
    path = _tmp_db_path()
    conn = pg_compat.connect(path)
    c = conn.cursor()
    c.execute("BEGIN IMMEDIATE")
    conn2 = pg_compat.connect(path)
    c2 = conn2.cursor()
    import hashlib
    lock_key = hashlib.md5(("begin:" + str(path)).encode("utf-8")).hexdigest()[:8]
    c2.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext(%s) & 2147483647, %s)",
        (lock_key, 1))
    assert c2.fetchone()[0] == 0, "advisory lock was not held during BEGIN IMMEDIATE"
    # Releasing the transaction releases the lock.
    conn.commit()
    c2.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext(%s) & 2147483647, %s)",
        (lock_key, 1))
    assert c2.fetchone()[0] == 1, "advisory lock was not released on commit"
    conn.close()
    conn2.close()


def test_changes_reports_previous_rowcount():
    conn = pg_compat.connect(_tmp_db_path())
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS ch_t(id TEXT PRIMARY KEY, v TEXT)")
    c.execute("INSERT INTO ch_t VALUES('a','x')")
    c.execute("INSERT INTO ch_t VALUES('b','x')")
    c.execute("INSERT INTO ch_t VALUES('c','x')")
    c.execute("UPDATE ch_t SET v='y' WHERE id IN ('a','b')")
    c.execute("SELECT changes()")
    assert c.fetchone()[0] == 2
    conn.close()


def test_pragma_table_info_shape():
    conn = pg_compat.connect(_tmp_db_path())
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS ti_t(id TEXT PRIMARY KEY, name TEXT)")
    c.execute("PRAGMA table_info(ti_t)")
    rows = c.fetchall()
    names = [r[1] for r in rows]
    assert names == ["id", "name"]
    conn.close()


def test_arabic_text_round_trip():
    conn = pg_compat.connect(_tmp_db_path())
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS ar_t(id TEXT PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO ar_t VALUES(?,?)", ("1", "محفظتي الرقمية — سوق نقاء"))
    c.execute("SELECT name FROM ar_t WHERE id=?", ("1",))
    assert c.fetchone()[0] == "محفظتي الرقمية — سوق نقاء"
    conn.close()


def test_rollback_atomicity_across_ddl():
    """Regression: DDL must not commit unrelated pending writes.

    A successful DDL, a failed (duplicate column) DDL, and a following
    rollback must all leave the earlier INSERT rolled back — sqlite3
    semantics.
    """
    conn = pg_compat.connect(_tmp_db_path())
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS atom_t(id TEXT PRIMARY KEY)")
    conn.commit()
    # Successful DDL inside the same transaction as an INSERT.
    c.execute("INSERT INTO atom_t VALUES('x')")
    c.execute("ALTER TABLE atom_t ADD COLUMN extra TEXT")
    conn.rollback()
    c.execute("SELECT COUNT(*) FROM atom_t")
    assert c.fetchone()[0] == 0, "successful DDL committed the pending INSERT"
    # Failed DDL must not commit pending work either.  The duplicate-column
    # error requires the column to actually exist, so commit it first.
    c.execute("ALTER TABLE atom_t ADD COLUMN extra TEXT")
    conn.commit()
    c.execute("INSERT INTO atom_t VALUES('y')")
    with pytest.raises(pg_compat.OperationalError):
        c.execute("ALTER TABLE atom_t ADD COLUMN extra TEXT")  # duplicate column
    conn.rollback()
    c.execute("SELECT COUNT(*) FROM atom_t")
    assert c.fetchone()[0] == 0, "failed DDL committed the pending INSERT"
    conn.close()


def test_data_survives_process_exit():
    """Regression: schemas must never be dropped at process exit.

    A worker writing data and exiting (restart/deploy) must not delete the
    data — the next process has to find it.  Child processes run without
    pytest so they use the stable production-style schema.
    """
    import subprocess
    import sys as _sys
    db_path = os.path.join(tempfile.mkdtemp(), "persist.db")
    child = (
        "import os, sys\n"
        "os.environ['DATABASE_PATH'] = sys.argv[1]\n"
        "import pg_compat\n"
        "assert pg_compat.is_pg_mode()\n"
        "conn = pg_compat.connect(sys.argv[1])\n"
        "c = conn.cursor()\n"
        "if sys.argv[2] == 'write':\n"
        "    c.execute('CREATE TABLE IF NOT EXISTS persist_t(id TEXT PRIMARY KEY, v TEXT)')\n"
        "    c.execute(\"INSERT INTO persist_t VALUES('1','survived')\")\n"
        "    conn.commit()\n"
        "else:\n"
        "    c.execute(\"SELECT v FROM persist_t WHERE id='1'\")\n"
        "    row = c.fetchone()\n"
        "    print(row[0] if row else 'MISSING')\n"
        "conn.close()\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r1 = subprocess.run([_sys.executable, "-c", child, db_path, "write"],
                        capture_output=True, text=True, env=env)
    assert r1.returncode == 0, r1.stderr
    r2 = subprocess.run([_sys.executable, "-c", child, db_path, "read"],
                        capture_output=True, text=True, env=env)
    assert r2.returncode == 0, r2.stderr
    assert r2.stdout.strip() == "survived", \
        "data was lost when the writing process exited (schema cleanup bug)"


def test_each_connection_gets_isolated_schema():
    a = pg_compat.connect(os.path.join(tempfile.mkdtemp(), "iso_a.db"))
    b = pg_compat.connect(os.path.join(tempfile.mkdtemp(), "iso_b.db"))
    ca, cb = a.cursor(), b.cursor()
    ca.execute("CREATE TABLE IF NOT EXISTS iso_t(id TEXT)")
    ca.execute("INSERT INTO iso_t VALUES('only-in-a')")
    a.commit()
    cb.execute("CREATE TABLE IF NOT EXISTS iso_t(id TEXT)")
    b.commit()
    cb.execute("SELECT COUNT(*) FROM iso_t")
    # b has its own schema: the row inserted via a must not be visible there.
    count = cb.fetchone()[0]
    assert count == 0
    a.close()
    b.close()
