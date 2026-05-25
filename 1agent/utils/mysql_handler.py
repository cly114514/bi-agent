from __future__ import annotations

import pymysql
import json
import re
from typing import Any
from utils.logger_handler import logger


def _safe_col_name(name: str) -> str:
    # 保留中文、字母、数字、下划线，其余替换为下划线
    name = re.sub(r"[^\w\u4e00-\u9fff]", "_", str(name))
    name = re.sub(r"_+", "_", name).strip("_")
    return f"`{name[:64]}`"


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
        self._ensure_db()

    def _connect(self):
        return _connect_raw(self.host, self.port, self.user, self.password, self.database)

    def _ensure_db(self):
        conn = _connect_raw(self.host, self.port, self.user, self.password)
        conn.cursor().execute(
            f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4"
        )
        conn.close()

    def create_table_from_excel(self, table_name: str, headers: list[str], sample_rows: list[list]) -> str:
        safe_name = table_name.replace(" ", "_").replace("-", "_")[:64]
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS `{safe_name}`")

        cols = []
        for i, h in enumerate(headers):
            col = _safe_col_name(h)
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
            sql_type = "DOUBLE" if is_numeric else "TEXT"
            cols.append(f"{col} {sql_type}")

        create_sql = f"CREATE TABLE `{safe_name}` (id INT AUTO_INCREMENT PRIMARY KEY, {', '.join(cols)})"
        cur.execute(create_sql)
        conn.commit()
        conn.close()
        return safe_name

    def insert_rows(self, table_name: str, headers: list[str], rows: list[list]):
        safe_name = table_name[:64]
        conn = self._connect()
        cur = conn.cursor()
        safe_headers = [_safe_col_name(h) for h in headers]
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
        conn.close()

    def execute_query(self, sql: str) -> dict:
        conn = self._connect()
        cur = conn.cursor()
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
            return {"success": False, "error": str(e), "sql": sql}
        finally:
            conn.close()


_mysql: MySQLHandler | None = None


def get_mysql() -> MySQLHandler | None:
    return _mysql


def init_mysql(host: str, port: int, user: str, password: str, database: str):
    global _mysql
    _mysql = MySQLHandler(host, port, user, password, database)
    logger.info(f"[MySQL] 已连接 {host}:{port}/{database}")
    return _mysql
