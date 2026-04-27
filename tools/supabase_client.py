"""
Shared Supabase client for Hermes TCG tools, fetcher runner, and hooks.

Originally a thin wrapper around supabase-py. This version adds a
direct-Postgres fallback so the runner works even when SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY aren't reliably injected to the gateway runtime
(see write_env.py line 116 comment: "SUPABASE_URL is NOT reliably injected").

Detection order at first call to get_client():
  1. POSTGRES_HOST + POSTGRES_USER + POSTGRES_DB present (NodeOps native
     Supabase integration) → psycopg2 connection pool, wrapped in a small
     query builder that mimics supabase-py's REST interface.
  2. SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY present → supabase-py SDK.
  3. Neither → return None and warn. Tools gated on `is_available()` will
     refuse to register / refuse to run.

The wrapper covers the subset of supabase-py used by:
  - tools/fetchers/runner.py
  - tools/fetchers/tools_api.py
  - tools/supabase_tcg.py

Specifically: .table(name) → builder with .select / .insert / .update /
.delete / .eq / .neq / .gt / .gte / .lt / .lte / .like / .ilike /
.in_ / .order / .limit / .single / .execute. The execute() result has a
`.data` attribute (list[dict] for normal queries, single dict or None
when .single() was called).

Not supported (extend if a caller needs them): rpc, storage, auth,
realtime, range/offset queries, contains/containedBy, full-text search,
joins/embedded resources, returning='minimal', count modes other than
exact-on-RETURNING.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

_client: Any = None


# ─── Public API ────────────────────────────────────────────────────────────


def get_client():
    """Return a cached client (Postgres-backed wrapper or supabase-py), or None."""
    global _client
    if _client is not None:
        return _client

    pg_host = os.getenv("POSTGRES_HOST", "").strip()
    pg_user = os.getenv("POSTGRES_USER", "").strip()
    pg_db = os.getenv("POSTGRES_DB", "").strip()

    if pg_host and pg_user and pg_db:
        try:
            _client = _PgClient.from_env()
            logger.info("[supabase] Using direct Postgres at %s", pg_host)
            return _client
        except Exception as e:
            logger.error("[supabase] Postgres init failed: %s — falling through to supabase-py", e)
            _client = None

    sb_url = os.getenv("SUPABASE_URL", "").strip()
    sb_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            _client = create_client(sb_url, sb_key)
            logger.info("[supabase] Using supabase-py SDK at %s", sb_url)
            return _client
        except Exception as e:
            logger.error("[supabase] supabase-py init failed: %s", e)
            _client = None
            return None

    logger.warning(
        "[supabase] Neither POSTGRES_* nor SUPABASE_URL+SUPABASE_SERVICE_ROLE_KEY "
        "set — Supabase tools will be unavailable."
    )
    return None


def is_available() -> bool:
    """Check whether a client can be instantiated without actually creating one."""
    pg_host = os.getenv("POSTGRES_HOST", "").strip()
    pg_user = os.getenv("POSTGRES_USER", "").strip()
    pg_db = os.getenv("POSTGRES_DB", "").strip()
    if pg_host and pg_user and pg_db:
        return True
    sb_url = os.getenv("SUPABASE_URL", "").strip()
    sb_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return bool(sb_url and sb_key)


# ─── Postgres-backed compatibility wrapper ─────────────────────────────────
#
# psycopg2 imports are deferred to first-use so this module loads even when
# the package isn't installed (matters during AST discovery + tests).


class _Response:
    """Mimics supabase-py's APIResponse: exposes .data (and .count when set)."""
    __slots__ = ("data", "count")

    def __init__(self, data: Any, count: Optional[int] = None):
        self.data = data
        self.count = count


class _PgClient:
    """Minimal supabase-py compatible client backed by a psycopg2 pool."""

    def __init__(self, pool):
        self._pool = pool

    @classmethod
    def from_env(cls) -> "_PgClient":
        from psycopg2.pool import ThreadedConnectionPool

        host = os.getenv("POSTGRES_HOST", "").strip()
        port = int(os.getenv("POSTGRES_PORT", "5432") or "5432")
        user = os.getenv("POSTGRES_USER", "").strip()
        password = os.getenv("POSTGRES_PASSWORD", "")
        database = os.getenv("POSTGRES_DB", "").strip()
        sslmode = os.getenv("POSTGRES_SSLMODE", "require").strip() or "require"

        pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=4,
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database,
            sslmode=sslmode,
        )
        return cls(pool)

    def table(self, name: str) -> "_PgTable":
        return _PgTable(self._pool, name)


class _PgTable:
    """Returned by client.table(name). Spawns a fresh _PgQuery per operation."""

    def __init__(self, pool, table_name: str):
        self._pool = pool
        self._table = table_name

    def select(self, cols: str = "*") -> "_PgQuery":
        return _PgQuery(self._pool, self._table, op="select", cols=cols)

    def insert(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]) -> "_PgQuery":
        return _PgQuery(self._pool, self._table, op="insert", payload=payload)

    def update(self, payload: Dict[str, Any]) -> "_PgQuery":
        return _PgQuery(self._pool, self._table, op="update", payload=payload)

    def delete(self) -> "_PgQuery":
        return _PgQuery(self._pool, self._table, op="delete")

    def upsert(
        self,
        payload: Union[Dict[str, Any], List[Dict[str, Any]]],
        on_conflict: Optional[str] = None,
    ) -> "_PgQuery":
        return _PgQuery(
            self._pool, self._table, op="upsert",
            payload=payload, on_conflict=on_conflict,
        )


class _PgQuery:
    """Builder for one query. Filter/order/limit methods return self for chaining."""

    _SUPPORTED_OPS = {"=", "<>", ">", ">=", "<", "<=", "LIKE", "ILIKE"}

    def __init__(
        self,
        pool,
        table_name: str,
        op: str,
        cols: str = "*",
        payload: Any = None,
        on_conflict: Optional[str] = None,
    ):
        self._pool = pool
        self._table = table_name
        self._op = op
        self._cols = cols
        self._payload = payload
        self._on_conflict = on_conflict
        self._filters: List[tuple] = []  # (op_str, col, val)
        self._order: Optional[tuple] = None  # (col, desc)
        self._limit: Optional[int] = None
        self._single = False

    # ── Filter methods ─────────────────────────────────────────────────
    def eq(self, col: str, val: Any) -> "_PgQuery":
        self._filters.append(("=", col, val)); return self

    def neq(self, col: str, val: Any) -> "_PgQuery":
        self._filters.append(("<>", col, val)); return self

    def gt(self, col: str, val: Any) -> "_PgQuery":
        self._filters.append((">", col, val)); return self

    def gte(self, col: str, val: Any) -> "_PgQuery":
        self._filters.append((">=", col, val)); return self

    def lt(self, col: str, val: Any) -> "_PgQuery":
        self._filters.append(("<", col, val)); return self

    def lte(self, col: str, val: Any) -> "_PgQuery":
        self._filters.append(("<=", col, val)); return self

    def like(self, col: str, pattern: str) -> "_PgQuery":
        self._filters.append(("LIKE", col, pattern)); return self

    def ilike(self, col: str, pattern: str) -> "_PgQuery":
        self._filters.append(("ILIKE", col, pattern)); return self

    def in_(self, col: str, vals: Sequence[Any]) -> "_PgQuery":
        self._filters.append(("IN", col, list(vals))); return self

    def order(self, col: str, desc: bool = False) -> "_PgQuery":
        self._order = (col, bool(desc)); return self

    def limit(self, n: int) -> "_PgQuery":
        self._limit = int(n); return self

    def single(self) -> "_PgQuery":
        self._single = True; return self

    # ── Execution ─────────────────────────────────────────────────────
    def execute(self) -> _Response:
        if self._op == "select":
            return self._run_select()
        if self._op == "insert":
            return self._run_insert()
        if self._op == "update":
            return self._run_update()
        if self._op == "delete":
            return self._run_delete()
        if self._op == "upsert":
            return self._run_upsert()
        raise ValueError(f"unknown op: {self._op!r}")

    # ── SQL builders ──────────────────────────────────────────────────
    def _select_cols_sql(self):
        from psycopg2 import sql
        if self._cols.strip() == "*":
            return sql.SQL("*")
        col_names = [c.strip() for c in self._cols.split(",") if c.strip()]
        return sql.SQL(", ").join(sql.Identifier(c) for c in col_names)

    def _where_sql(self):
        from psycopg2 import sql
        if not self._filters:
            return sql.SQL(""), []
        clauses = []
        params: List[Any] = []
        for op_str, col, val in self._filters:
            if op_str == "IN":
                if not val:
                    # IN () would be a SQL syntax error → force no-match
                    clauses.append(sql.SQL("FALSE"))
                    continue
                placeholders = sql.SQL("(") + sql.SQL(", ").join(
                    [sql.Placeholder()] * len(val)
                ) + sql.SQL(")")
                clauses.append(
                    sql.SQL("{col} IN ").format(col=sql.Identifier(col)) + placeholders
                )
                params.extend(val)
            elif op_str in self._SUPPORTED_OPS:
                clauses.append(
                    sql.SQL("{col} ").format(col=sql.Identifier(col))
                    + sql.SQL(op_str)
                    + sql.SQL(" {ph}").format(ph=sql.Placeholder())
                )
                params.append(val)
            else:
                raise ValueError(f"unsupported filter op: {op_str!r}")
        return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses), params

    def _order_limit_sql(self):
        from psycopg2 import sql
        out = sql.SQL("")
        order_params: List[Any] = []
        if self._order:
            col, desc = self._order
            direction = sql.SQL(" DESC") if desc else sql.SQL(" ASC")
            out = out + sql.SQL(" ORDER BY {col}").format(col=sql.Identifier(col)) + direction
        if self._limit is not None:
            out = out + sql.SQL(" LIMIT %s")
            order_params.append(self._limit)
        return out, order_params

    @staticmethod
    def _wrap_jsonb(val: Any):
        """Wrap dicts/lists for JSONB columns; pass other values through."""
        from psycopg2.extras import Json
        if isinstance(val, (dict, list)):
            return Json(val)
        return val

    # ── select / insert / update / delete / upsert runners ────────────
    def _run_select(self) -> _Response:
        from psycopg2 import sql
        cols_sql = self._select_cols_sql()
        where_sql, where_params = self._where_sql()
        ord_sql, ord_params = self._order_limit_sql()

        query = (
            sql.SQL("SELECT {cols} FROM {table}").format(
                cols=cols_sql, table=sql.Identifier(self._table)
            )
            + where_sql
            + ord_sql
        )

        # If .single() was used and no explicit limit, cap at LIMIT 2 so we
        # can detect ambiguity (>1 row matched). This mirrors supabase-py.
        if self._single and self._limit is None:
            query = query + sql.SQL(" LIMIT 2")

        return self._exec(query, where_params + ord_params, returns_rows=True)

    def _run_insert(self) -> _Response:
        from psycopg2 import sql
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        if not rows:
            return _Response([])

        # Union of all column names across rows so heterogeneous payloads work.
        col_set = set()
        for r in rows:
            col_set.update(r.keys())
        col_list = sorted(col_set)

        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in col_list)
        row_template = sql.SQL("(") + sql.SQL(", ").join(
            [sql.Placeholder()] * len(col_list)
        ) + sql.SQL(")")
        values_sql = sql.SQL(", ").join([row_template] * len(rows))

        params: List[Any] = []
        for r in rows:
            for c in col_list:
                params.append(self._wrap_jsonb(r.get(c)))

        query = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES "
        ).format(
            table=sql.Identifier(self._table), cols=cols_sql,
        ) + values_sql + sql.SQL(" RETURNING *")

        return self._exec(query, params, returns_rows=True)

    def _run_update(self) -> _Response:
        from psycopg2 import sql
        if not self._payload:
            return _Response([])
        col_list = sorted(self._payload.keys())
        set_clause = sql.SQL(", ").join(
            sql.SQL("{col} = {ph}").format(
                col=sql.Identifier(c), ph=sql.Placeholder()
            )
            for c in col_list
        )
        params: List[Any] = [self._wrap_jsonb(self._payload[c]) for c in col_list]

        where_sql, where_params = self._where_sql()
        params.extend(where_params)

        query = sql.SQL("UPDATE {table} SET ").format(
            table=sql.Identifier(self._table)
        ) + set_clause + where_sql + sql.SQL(" RETURNING *")
        return self._exec(query, params, returns_rows=True)

    def _run_delete(self) -> _Response:
        from psycopg2 import sql
        where_sql, params = self._where_sql()
        query = sql.SQL("DELETE FROM {table}").format(
            table=sql.Identifier(self._table)
        ) + where_sql + sql.SQL(" RETURNING *")
        return self._exec(query, params, returns_rows=True)

    def _run_upsert(self) -> _Response:
        from psycopg2 import sql
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        if not rows:
            return _Response([])

        col_set = set()
        for r in rows:
            col_set.update(r.keys())
        col_list = sorted(col_set)

        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in col_list)
        row_template = sql.SQL("(") + sql.SQL(", ").join(
            [sql.Placeholder()] * len(col_list)
        ) + sql.SQL(")")
        values_sql = sql.SQL(", ").join([row_template] * len(rows))

        params: List[Any] = []
        for r in rows:
            for c in col_list:
                params.append(self._wrap_jsonb(r.get(c)))

        # ON CONFLICT target: explicit column list, or ON CONFLICT DO NOTHING
        # if none was given.
        if self._on_conflict:
            conflict_cols = [c.strip() for c in self._on_conflict.split(",") if c.strip()]
            conflict_sql = sql.SQL(" ON CONFLICT (") + sql.SQL(", ").join(
                sql.Identifier(c) for c in conflict_cols
            ) + sql.SQL(") DO UPDATE SET ") + sql.SQL(", ").join(
                sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
                for c in col_list if c not in conflict_cols
            )
        else:
            conflict_sql = sql.SQL(" ON CONFLICT DO NOTHING")

        query = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES "
        ).format(
            table=sql.Identifier(self._table), cols=cols_sql,
        ) + values_sql + conflict_sql + sql.SQL(" RETURNING *")

        return self._exec(query, params, returns_rows=True)

    # ── Connection-pool execute ───────────────────────────────────────
    def _exec(self, query, params, returns_rows: bool) -> _Response:
        from psycopg2.extras import RealDictCursor

        conn = self._pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                if returns_rows:
                    raw = cur.fetchall()
                    rows = [dict(r) for r in raw]
                else:
                    rows = []
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

        if self._single:
            if not rows:
                return _Response(None)
            # supabase-py raises if .single() matched >1 row; we just take the
            # first to keep the runner unblocked. Log if ambiguous.
            if len(rows) > 1:
                logger.warning(
                    "[supabase] .single() matched %d rows on %s — returning first",
                    len(rows), self._table,
                )
            return _Response(rows[0])
        return _Response(rows)
