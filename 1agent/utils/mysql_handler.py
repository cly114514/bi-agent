from __future__ import annotations

import hashlib
import pymysql
import re
import threading
from typing import Any
from utils.excel_parser import _NUMERIC_FIELD_KEYWORDS
from utils.logger_handler import logger


def _safe_col_name(name: str, used: set[str] | None = None) -> str:
    """Sanitize a column name for use in a MySQL DDL statement.

    - Keeps CJK + word chars; replaces everything else with ``_``.
    - If the sanitized name is longer than 64 chars, append an 8-char
      MD5 digest of the full sanitized name so it still fits in
      MySQL's identifier limit (and is unique enough to avoid silent
      truncation collisions; see #6).
    - If ``used`` is provided, append ``_2``, ``_3``... to resolve
      collisions inside the same table.
    """
    name = re.sub(r"[^\w一-鿿]", "_", str(name))
    name = re.sub(r"_+", "_", name).strip("_") or "col"
    if len(name) > 64:
        digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        name = f"{name[:55]}_{digest}"
    if used is not None:
        base = name
        i = 2
        while name in used:
            suffix = f"_{i}"
            # If base is at the 64-char limit, trim before appending
            trim = 64 - len(suffix)
            name = f"{base[:trim]}{suffix}"
            i += 1
        used.add(name)
    return f"`{name}`"


def _is_amount_column(name: str) -> bool:
    return any(kw in name for kw in _NUMERIC_FIELD_KEYWORDS)


def _connect_raw(host: str, port: int, user: str, password: str, database: str = ""):
    kwargs = dict(
        host=host, port=port, user=user, password=password,
        charset="utf8mb4",
    )
    if database:
        kwargs["database"] = database
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**kwargs)


class MySQLHandler:
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        # #24: per-thread connection cache. Avoids the ~10-50ms connect
        # cost on every query without requiring the dbutils dependency.
        # A simple ``threading.local`` pool sized to the calling thread
        # count is sufficient for this single-process Streamlit app.
        self._local = threading.local()
        self._ensure_db()

    def _connect(self):
        """Return a connection from the per-thread pool, opening a new
        one if needed. Stale connections (closed by server) are
        transparently reconnected.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.ping(reconnect=True)
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None
        conn = _connect_raw(self.host, self.port, self.user, self.password, self.database)
        self._local.conn = conn
        return conn

    def close(self):
        """Close the per-thread connection (if any)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def _ensure_db(self):
        conn = _connect_raw(self.host, self.port, self.user, self.password)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4"
                )
            conn.commit()
        finally:
            conn.close()

    def create_table_from_excel(self, table_name: str, headers: list[str], sample_rows: list[list]) -> str:
        safe_name = table_name.replace(" ", "_").replace("-", "_")[:64]
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS `{safe_name}`")
                used: set[str] = set()
                cols = []
                for i, h in enumerate(headers):
                    col = _safe_col_name(h, used)
                    numeric_count = 0
                    total = 0
                    for row in sample_rows:
                        if i < len(row) and row[i] is not None:
                            total += 1
                            try:
                                float(str(row[i]))
                                numeric_count += 1
                            except (ValueError, TypeError):
                                pass
                    is_numeric = total > 0 and numeric_count >= total * 0.5
                    # #29: use DECIMAL(20,4) for amount-like columns to
                    # avoid float precision loss for currency
                    if is_numeric and _is_amount_column(h):
                        sql_type = "DECIMAL(20,4)"
                    elif is_numeric:
                        sql_type = "DOUBLE"
                    else:
                        sql_type = "TEXT"
                    cols.append(f"{col} {sql_type}")
                create_sql = f"CREATE TABLE `{safe_name}` (id INT AUTO_INCREMENT PRIMARY KEY, {', '.join(cols)})"
                cur.execute(create_sql)
            conn.commit()
        finally:
            conn.close()
        return safe_name

    def insert_rows(self, table_name: str, headers: list[str], rows: list[list]):
        safe_name = table_name[:64]
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                used: set[str] = set()
                safe_headers = [_safe_col_name(h, used) for h in headers]
                placeholders = ", ".join(["%s"] * len(headers))
                cols = ", ".join(safe_headers)
                sql = f"INSERT INTO `{safe_name}` ({cols}) VALUES ({placeholders})"
                inserted = 0
                for row in rows:
                    try:
                        cur.execute(sql, row)
                        inserted += 1
                    except Exception as e:
                        logger.warning(f"[MySQL] INSERT failed for row: {e}")
            conn.commit()
            logger.info(f"[MySQL] Inserted {inserted}/{len(rows)} rows into {safe_name}")
        finally:
            conn.close()

    def execute_query(self, sql: str) -> dict:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    return {
                        "success": True,
                        "row_count": len(rows),
                        "columns": [d[0] for d in cur.description] if cur.description else [],
                        "data": rows,
                        "sql": sql,
                    }
                except Exception as e:
                    logger.error(f"[MySQL] SQL 执行失败: {e}", exc_info=True)
                    return {
                        "success": False,
                        "error": _friendly_mysql_error(e),
                        "sql": sql,
                    }
        finally:
            conn.close()


def _friendly_mysql_error(e: Exception) -> str:
    """Map a raw pymysql exception to a Chinese-friendly message so the
    UI never leaks driver internals (e.g. SQL fragments, host names) to
    the user (#30)."""
    msg = str(e)
    code = getattr(e, "args", [None])[0]
    if code in (1045,):  # access denied
        return "MySQL 认证失败: 用户名或密码错误"
    if code in (1049,):  # unknown database
        return "MySQL 数据库不存在"
    if code in (1044,):  # access denied to db
        return "MySQL 用户无权访问该数据库"
    if code in (1146,):  # table doesn't exist
        return "数据表不存在, 请先导入 Excel"
    if code in (1064,):  # SQL syntax error
        return "SQL 语法错误, 请检查查询语句"
    if code in (1054,):  # unknown column
        return "SQL 引用了不存在的列"
    if code in (2003,) or "Connection refused" in msg or "Can't connect" in msg:
        return "MySQL 连接被拒绝, 请检查服务是否启动 / 端口是否正确"
    if "timed out" in msg.lower():
        return "MySQL 连接超时, 请检查网络"
    # Fall back to the type name + a sanitized tail (no stack trace, no
    # full SQL) so the user still has a hint without information leak.
    return f"MySQL 错误 ({type(e).__name__})"


_mysql: MySQLHandler | None = None


def get_mysql() -> MySQLHandler | None:
    return _mysql


def init_mysql(host: str, port: int, user: str, password: str, database: str):
    global _mysql
    _mysql = MySQLHandler(host, port, user, password, database)
    logger.info(f"[MySQL] 已连接 {host}:{port}/{database}")
    return _mysql
