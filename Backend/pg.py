"""PostgreSQL driver for the Study Planner persistence layer.

A thin shim around psycopg2 that presents the same surface the rest of the
codebase uses with the SQLite ``sqlite3`` module (``conn.execute(...)``,
``cursor.fetchall()/fetchone()/rowcount/lastrowid``, ``executemany``,
``executescript``, ``commit``), so ``db.connect()`` can return Postgres or
SQLite without callers knowing the difference.

SQL written with``?`` placeholders (SQLite dialect) is translated on the fly
to Postgres parameter style and the handful of SQLite idioms used across the
app (``INSERT OR IGNORE``, ``INSERT OR REPLACE``, ``datetime('now')``,
``ORDER BY rowid``) are rewritten to their Postgres equivalents.
"""

import re

import psycopg2
import psycopg2.extras

# INSERT OR IGNORE -> plain INSERT + ON CONFLICT DO NOTHING (used by the
# memberships / courses / labels / dependencies upserts).
_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_OR_REPLACE = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO", re.IGNORECASE)

# Single-column-PK tables that use INSERT OR REPLACE. The conflict target is
# that column; on conflict we refresh every other column.
_REPLACE_PKS = {
    "app_config": "key",
    "subject_codes": "slug",
}

_ORDER_ROWID_KEY = re.compile(r"\border\s+by\s+rowid\b", re.IGNORECASE)
_ORDER_ROWID_DESC = re.compile(r"\border\s+by\s+rowid\s+desc\b", re.IGNORECASE)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([`\"]?\w+[`\"]?)",
    re.IGNORECASE,
)
_INSERT_INTO_RE = re.compile(
    r"^\s*INSERT\s+INTO\s+([`\"]?\w+[`\"]?)",
    re.IGNORECASE,
)

# Tables whose id column is a Postgres SERIAL. Only those can use ``lastrowid``
# via ``INSERT ... RETURNING id``; TEXT-PK tables have no sequence to consult.
_SERIAL_TABLES = set()
_schema_configured = False


def configure_schema(script):
    """Register which tables get ``SERIAL PRIMARY KEY`` ids from a schema script
    (the Postgres variant of the app schema). Idempotent."""
    global _schema_configured
    if _schema_configured:
        return
    _SERIAL_TABLES.clear()
    for stmt in _split_script(script):
        # Statements may carry a leading comment block (prose before the DDL);
        # the CREATE TABLE is the last match in the statement.
        matches = list(_CREATE_TABLE_RE.finditer(stmt))
        if not matches:
            continue
        m = matches[-1]
        table = m.group(1).strip('`"').lower()
        body = stmt[m.end():]
        if re.search(r"^\s*id\s+SERIAL\s+PRIMARY\s+KEY",
                     body, re.IGNORECASE | re.MULTILINE):
            _SERIAL_TABLES.add(table)
    _schema_configured = True


def _serial_insert_rewrite(sql):
    """Return (sql, table) with ``RETURNING id`` appended when the insert
    targets a SERIAL-id table, so ``lastrowid`` can be read from the result
    row. Never touches non-serial or already-RETURNING statements."""
    m = _INSERT_INTO_RE.match(sql)
    if not m:
        return None, None
    table = m.group(1).strip('`"').lower()
    if table not in _SERIAL_TABLES or " RETURNING " in sql.upper():
        return None, None
    return sql.rstrip(" \t;") + " RETURNING id", table


def _translate(sql):
    """Convert a SQLite-dialect statement into Postgres syntax."""
    if _OR_IGNORE.search(sql):
        sql = _OR_IGNORE.sub("INSERT INTO", sql)
        # 'INSERT ... VALUES (...) ON CONFLICT DO NOTHING'
        sql = sql.rstrip(";") + " ON CONFLICT DO NOTHING"
    elif _OR_REPLACE.search(sql):
        m = _OR_REPLACE.search(sql)
        rest = sql[m.end():].lstrip()
        table = re.match(r"([`\"]?\w+[`\"]?)", rest).group(1).strip('`"')
        pk = _REPLACE_PKS.get(table.lower())
        if pk:
            sql = _OR_REPLACE.sub("INSERT INTO", sql).rstrip(";")
            sql += f" ON CONFLICT ({pk}) DO UPDATE SET {pk} = excluded.{pk}"
        else:
            sql = _OR_REPLACE.sub("INSERT INTO", sql).rstrip(";")
            sql += " ON CONFLICT DO NOTHING"

    sql = sql.replace("datetime('now')", "CURRENT_TIMESTAMP")
    sql = _ORDER_ROWID_DESC.sub("ORDER BY created_at DESC, id DESC", sql)
    sql = _ORDER_ROWID_KEY.sub("ORDER BY created_at, id", sql)
    sql = sql.replace("?", "%s")
    return sql


def _split_script(script):
    """Split a SQL script on top-level semicolons.

    Comment/quote aware: a ``;`` inside a ``--`` comment or a string literal is
    not a statement boundary (the schema has both — e.g. prose comments that
    contain semicolons), and comment-only fragments are dropped so psycopg2 is
    never handed an empty query.
    """
    stmts = []
    buf = []
    i = 0
    n = len(script)
    in_single = in_double = in_line = in_block = False
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if in_line:
            buf.append(ch)
            if ch == "\n":
                in_line = False
            i += 1
        elif in_block:
            if ch == "*" and nxt == "/":
                buf.append(ch)
                buf.append(nxt)
                in_block = False
                i += 2
            else:
                buf.append(ch)
                i += 1
        elif in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                else:
                    in_single = False
                    i += 1
            else:
                i += 1
        elif in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
        elif ch == "-" and nxt == "-":
            in_line = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
        elif ch == "/" and nxt == "*":
            in_block = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
        elif ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
        elif ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
        elif ch == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    stmts.append("".join(buf))
    return [s for s in stmts if _has_sql(s)]


def _has_sql(stmt):
    """True when ``stmt`` contains executable SQL (not just comments/space)."""
    i = 0
    n = len(stmt)
    in_single = in_double = in_line = in_block = False
    while i < n:
        ch = stmt[i]
        nxt = stmt[i + 1] if i + 1 < n else ""
        if in_line:
            if ch == "\n":
                in_line = False
            i += 1
        elif in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
            else:
                i += 1
        elif in_single:
            if ch == "'":
                if nxt == "'":
                    i += 2
                else:
                    in_single = False
                    i += 1
            else:
                i += 1
        elif in_double:
            if ch == '"':
                in_double = False
            i += 1
        elif ch == "-" and nxt == "-":
            in_line = True
            i += 2
        elif ch == "/" and nxt == "*":
            in_block = True
            i += 2
        elif ch == "'":
            in_single = True
            i += 1
        elif ch == '"':
            in_double = True
            i += 1
        else:
            if not ch.isspace():
                return True
            i += 1
    return False


class _Cursor:
    """Wraps a psycopg2 cursor so ``lastrowid``/``rowcount`` look SQLite-like."""

    def __init__(self, cursor, connection):
        self._cur = cursor
        self._conn = connection
        self.lastrowid = None

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for r in self._cur:
            yield dict(r)

    @property
    def rowcount(self):
        return self._cur.rowcount


class PgConnection:
    """sqlite3-style facade over a psycopg2 connection."""

    def __init__(self, dsn):
        self._conn = psycopg2.connect(dsn)

    def execute(self, sql, params=()):
        sql = _translate(sql)
        return self._with_cursor(sql, params)

    def executemany(self, sql, seq):
        sql = _translate(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.executemany(sql, seq)
        except Exception:
            self._conn.rollback()
            raise
        wrapper = _Cursor(cur, self._conn)
        wrapper.lastrowid = None
        return wrapper

    def executescript(self, script):
        cursor = self._conn.cursor()
        try:
            for statement in _split_script(script):
                statement = statement.strip()
                if statement:
                    cursor.execute(_translate(statement))
        except Exception:
            self._conn.rollback()
            raise
        self._conn.commit()
        return _Cursor(cursor, self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    # -- internals ---------------------------------------------------------

    def _with_cursor(self, sql, params):
        out_sql, serial_table = _serial_insert_rewrite(sql)
        if out_sql is None:
            out_sql = sql
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(out_sql, params)
        except Exception:
            self._conn.rollback()
            raise
        wrapper = _Cursor(cur, self._conn)
        if serial_table is not None:
            # Mirror sqlite3 cursor.lastrowid for SERIAL-id inserts. The id
            # comes back from RETURNING (same transaction), never LASTVAL —
            # a failed LASTVAL would poison the open insert transaction.
            try:
                row = cur.fetchone()
                wrapper.lastrowid = row["id"] if row else None
            except Exception:
                wrapper.lastrowid = None
        else:
            wrapper.lastrowid = None
        return wrapper


def connect(dsn):
    """Open a Postgres connection wrapper from a DATABASE_URL-style DSN."""
    return PgConnection(dsn)