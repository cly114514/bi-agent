"""Agent tools for the BI agent.

This module provides four LangChain @tool-decorated functions used by the
ReAct-style agent loop:

- get_table_schema: returns a textual description of the currently loaded
  Excel schemas (multi-file aware). If ``file_id`` is given, returns the
  full schema for that file; otherwise returns a "directory" view
  listing every loaded file and its sheets.
- generate_sql:    calls RagSummarizeService.rag_summarize to retrieve
  similar SQL examples from ChromaDB and prompt the LLM to produce SQL.
- execute_sql:     runs SQL against MySQL. Accepts an optional ``file_id``
  parameter so the table-name rewrite can be scoped to a specific
  uploaded file. Auto-rewrites RAG placeholder table names, applies the
  English→Chinese column alias map, and injects WHERE clauses for
  dirty-value exclusion (all via sqlglot AST).
- execute_excel:   fallback when MySQL is not connected. Accepts an
  optional ``file_id``. Delegates to
  ``utils.excel_query.execute_excel_query`` (a rule-based NL→filter/
  aggregator engine, not LLM-driven).

# Multi-file design:
# State is a dict ``_current_files: {file_id: {filename, sheets, ...}}``.
# ``file_id`` is the upload filename (e.g. ``"销售数据.xlsx"``) or a
# uuid-based id when multiple uploads share a name. ``set_files()``
# atomically replaces the dict. ``get_table_schema()`` with no
# ``file_id`` returns a directory view; with a ``file_id`` it returns
# the detailed view for that file.
#
# #5: state is still module-level for backwards compatibility. All
# readers/writers are protected by ``_state_lock`` so concurrent tool
# invocations from different threads see a consistent snapshot.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from langchain_core.tools import tool

import sqlglot
from sqlglot import exp

from rag.rag_service import RagSummarizeService
from utils.mysql_handler import get_mysql
from utils.logger_handler import logger

# Lazy RAG service singleton
rag: RagSummarizeService | None = None


def _get_rag() -> RagSummarizeService:
    global rag
    if rag is None:
        rag = RagSummarizeService()
    return rag


# ── Module-level session state (per-process; see #5) ──
_state_lock = threading.Lock()
# Multi-file state: file_id -> {"filename": str, "sheets": [...], "table_name": str}
_current_files: dict[str, dict] = {}
# Backwards-compat aliases (single-file views used by older app.py paths)
_current_schema: dict = {}
_current_table_name: str = ""


def set_files(files: list[dict]) -> None:
    """Replace the entire multi-file state.

    Each entry in ``files`` should be the parsed Excel dict as returned
    by ``utils.excel_parser.parse_excel_bytes`` plus an optional
    ``"table_name"`` key set after MySQL import. We index by the
    ``file_id`` (passed as ``id`` key on each entry) or fall back to
    the filename.
    """
    with _state_lock:
        global _current_files, _current_schema, _current_table_name
        new_state: dict[str, dict] = {}
        for f in files:
            file_id = f.get("id") or f.get("filename") or "default"
            new_state[file_id] = dict(f)
        _current_files = new_state
        # Keep single-file aliases in sync for code that hasn't migrated
        if new_state:
            first_id, first = next(iter(new_state.items()))
            _current_schema = first
            _current_table_name = first.get("table_name", "")
        else:
            _current_schema = {}
            _current_table_name = ""


def set_schema(schema: dict) -> None:
    """Backwards-compat: treat ``schema`` as a single-file upload named
    ``"default"``. Prefer :func:`set_files` for new callers."""
    set_files([{"id": "default", **schema}])


def set_table_name(name: str) -> None:
    """Backwards-compat: sets the table_name on the first loaded file."""
    with _state_lock:
        global _current_table_name
        _current_table_name = name
        if _current_files:
            first_id = next(iter(_current_files))
            _current_files[first_id]["table_name"] = name


def _snapshot() -> tuple[dict, dict, str]:
    """Atomically read the multi-file state."""
    with _state_lock:
        return (
            {fid: dict(f) for fid, f in _current_files.items()},
            dict(_current_schema),
            _current_table_name,
        )


def _get_file(file_id: str) -> tuple[str, dict] | None:
    """Look up a file by id (or filename). Returns (file_id, entry) or
    None if not found. If ``file_id`` is empty and exactly one file is
    loaded, returns that file (single-file convenience)."""
    files, _, _ = _snapshot()
    if not files:
        return None
    if file_id and file_id in files:
        return file_id, files[file_id]
    # Try matching by filename (some callers pass filename as file_id)
    if file_id:
        for fid, f in files.items():
            if f.get("filename") == file_id:
                return fid, f
    if not file_id and len(files) == 1:
        fid = next(iter(files))
        return fid, files[fid]
    return None


# ── RAG-style placeholder table names to rewrite to the actual MySQL
#    table name. Listed explicitly so a malicious column name like
#    ``users`` can't accidentally collide.
EXAMPLE_TABLES = (
    "contracts", "sales", "revenue", "orders", "users", "合同", "Sheet1",
    "employees", "inventory", "tickets", "returns",
    "daily_orders", "ads", "monthly_target", "preorders",
    "user_retention", "logistics", "purchase_orders",
    "marketing_campaign", "projects", "equipment_logs",
    "budget_tracking", "user_login", "sales_compare",
    "profit_month", "user_activity", "stores",
)

# English column-name aliases used in the RAG corpus.
COLUMN_ALIASES = {
    "contract_id": "合同ID", "purchase_quantity": "购买数量",
    "amount": "总金额", "total_amount": "总金额", "revenue": "总金额",
    "order_count": "购买数量", "product_name": "购买的产品",
    "region": "区域", "product_line": "产品线", "channel": "渠道",
    "conversion_rate": "转化率", "month": "合同签约时间",
    "salesperson": "客户ID", "contract_type": "合同类型",
    "customer_id": "客户ID", "payment_type": "合同付款类型",
    "registration_time": "注册时间", "purchased_product": "购买的产品",
    "delivery_status": "是否已经交货", "delivered": "是否已经交货",
}


@tool(description="生成SQL查询语句：根据用户需求和上传的Excel数据表字段，从向量库检索相似的SQL示例，生成精准的SQL查询语句")
def generate_sql(query: str) -> str:
    return _get_rag().rag_summarize(query)


@tool(description="执行查询（无MySQL时直接查Excel数据）。file_id 可选: 多文件场景下指定查询哪个上传的 Excel 文件。")
def execute_excel(query: str, file_id: str = "") -> str:
    """当MySQL不可用时，直接在上传的Excel数据上执行查询。

    ``file_id`` 为空时会用唯一加载的文件; 多文件场景下必须显式指定。
    """
    hit = _get_file(file_id)
    if not hit:
        return json.dumps({"success": False, "error": "尚未上传Excel数据"}, ensure_ascii=False)
    _, file_entry = hit
    from utils.excel_query import execute_excel_query
    result = execute_excel_query(file_entry, query)
    return json.dumps(result, ensure_ascii=False, default=str)


def _format_file_schema(file_entry: dict, file_id: str) -> list[str]:
    """Format a single file's schema (sheets + columns + profiles) as
    text lines for the LLM prompt."""
    lines = [f"文件ID: {file_id}  文件名: {file_entry.get('filename', '?')}"
             f"  MySQL表名: {file_entry.get('table_name', '(未导入)')}"]
    for sheet in file_entry.get("sheets", []):
        lines.append(f"  Sheet: {sheet['name']} ({sheet['rows']}行 x {sheet['columns']}列)")
        headers = [h for h in sheet.get("column_headers", []) if h]
        lines.append(f"  字段: {', '.join(headers)}")
        for col_name, profile in sheet.get("column_profiles", {}).items():
            dtype_str = profile.get("dtype", "empty")
            if dtype_str == "numeric" and "min" in profile:
                dtype_str += f"(范围{profile['min']}~{profile['max']})"
            samples = profile.get("sample_values", [])
            if samples:
                all_vals = " | ".join(samples)
                dtype_str += f" 所有唯一值=[{all_vals}]"
            dirty = profile.get("dirty_values", [])
            if dirty:
                dtype_str += f" ⚠️脏数据={dirty}"
            lines.append(f"    {col_name}: {dtype_str}")
    return lines


@tool(description="获取当前已上传数据表的字段结构信息。不传 file_id 时返回所有已加载文件的目录视图; 传 file_id 时返回该文件的详细字段信息。")
def get_table_schema(file_id: str = "") -> str:
    """返回所有已加载文件的 schema. 多文件时必须先用此工具不带 file_id 拿到目录, 再决定查询哪个文件."""
    files, _, _ = _snapshot()
    if not files:
        return "尚未上传数据表，请先上传Excel文件"
    if file_id:
        hit = _get_file(file_id)
        if not hit:
            return f"未找到文件ID={file_id!r}; 已加载文件: {list(files.keys())}"
        _, entry = hit
        return "\n".join(_format_file_schema(entry, file_id))
    # Directory view
    lines = [f"当前已加载 {len(files)} 个文件:"]
    for fid, entry in files.items():
        sheets = entry.get("sheets", [])
        sheet_names = [s.get("name", "?") for s in sheets]
        all_headers = []
        for s in sheets:
            for h in s.get("column_headers", []):
                if h and h not in all_headers:
                    all_headers.append(h)
        lines.append(
            f"- file_id={fid!r}  文件名={entry.get('filename', '?')!r}  "
            f"sheets={sheet_names}  字段总数={len(all_headers)}"
        )
    lines.append("")
    lines.append("多文件场景:")
    lines.append("- 涉及单文件 → 调 get_table_schema 时带 file_id, 或在 execute_sql/execute_excel 里带 file_id")
    lines.append("- 涉及多文件 JOIN → 直接写多表 FROM 即可, MySQL 会执行 JOIN")
    lines.append("- 不确定用户指哪个文件 → 调 [CLARIFY] 让用户选")
    return "\n".join(lines)


# ── Helpers used by execute_sql ──
_TABLE_NAME_RE = re.compile(r"^[\w一-鿿]+$")


def _safe_table_name(name: str) -> str:
    """Validate a table name; backtick-quote it. Reject anything not in the
    safe set (alnum + underscore + Chinese)."""
    if not _TABLE_NAME_RE.match(name):
        raise ValueError(f"非法表名: {name!r}")
    return f"`{name}`"


def _rewrite_sql_identifiers(sql: str, actual_table: str, valid_columns: set[str]) -> str:
    """Rewrite RAG placeholder identifiers in ``sql`` using sqlglot AST so
    we never touch string literals or comments. ``valid_columns`` is the
    set of real column names from the loaded Excel schema — only those
    aliases that map to a valid column get rewritten.

    For multi-file, the caller should pass the union of all loaded
    files' columns so the rewrite doesn't drop legitimate cross-file
    JOIN columns.
    """
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except sqlglot.errors.ParseError as e:
        logger.warning(f"[execute_sql] SQL 解析失败, 跳过重写: {e}")
        return sql

    changed = False

    # 1) Replace RAG placeholder table names with the actual MySQL table
    safe_actual = _safe_table_name(actual_table)
    for table in list(tree.find_all(exp.Table)):
        if table.name in EXAMPLE_TABLES:
            table.replace(exp.to_table(actual_table, db=table.args.get("db")))
            changed = True

    # 2) Replace English column aliases ONLY when the alias maps to a real
    #    column from the loaded schema. This prevents blowing up SQL like
    #    ``SELECT 'amount' AS note FROM t`` where 'amount' is a string
    #    literal, not an identifier.
    for col in list(tree.find_all(exp.Column)):
        name = col.name
        if not name:
            continue
        target = COLUMN_ALIASES.get(name.lower())
        if target and target in valid_columns:
            col.replace(exp.column(target))
            changed = True

    if not changed:
        return sql
    return tree.sql(dialect="mysql")


def _is_numeric_token(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _collect_dirty_filters(schema: dict) -> list[dict]:
    """Walk the schema and return one filter spec per column with dirty
    values. Each spec is::

        {"col": "`name`", "str_dirty": [...], "num_dirty": [...], "zeros": bool}
    """
    out: list[dict] = []
    for sheet in schema.get("sheets", []):
        for col_name, profile in sheet.get("column_profiles", {}).items():
            dirty = profile.get("dirty_values", []) or []
            if not dirty:
                continue
            str_dirty: list[str] = []
            num_dirty: list[str] = []
            for d in dirty:
                if not isinstance(d, str):
                    d = str(d)
                if _is_numeric_token(d):
                    num_dirty.append(d)
                else:
                    str_dirty.append(d)
            zeros = any(d in ("0", "0.0", "0.00") for d in num_dirty)
            out.append({
                "col": f"`{col_name}`",
                "str_dirty": str_dirty[:10],
                "num_dirty": num_dirty,
                "zeros": zeros,
            })
    return out


def _inject_dirty_where(sql: str, filters: list[dict]) -> tuple[str, int]:
    """Inject ``WHERE`` predicates for dirty-value exclusion using sqlglot
    AST. Returns ``(rewritten_sql, n_placeholders)``. The caller binds
    the placeholders via positional ``%s`` parameters (pymysql)."""
    if not filters:
        return sql, 0
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except sqlglot.errors.ParseError as e:
        logger.warning(f"[execute_sql] 解析失败, 跳过脏数据注入: {e}")
        return sql, 0

    new_clauses: list[exp.Expression] = []
    for f in filters:
        col_ref = exp.column(f["col"].strip("`"))
        if f["str_dirty"]:
            placeholders = [exp.placeholder(f"p{i}") for i in range(len(f["str_dirty"]))]
            in_expr = exp.In(this=col_ref, expressions=placeholders)
            new_clauses.append(exp.Not(this=in_expr))
        if f["zeros"]:
            new_clauses.append(exp.GT(this=col_ref.copy(), expression=exp.Literal.number(0)))

    if not new_clauses:
        return sql, 0

    combined: exp.Expression = new_clauses[0]
    for c in new_clauses[1:]:
        combined = exp.And(this=combined, expression=c)

    top_select = tree
    if not isinstance(top_select, (exp.Select, exp.Union)):
        return sql, 0
    existing_where = top_select.args.get("where")
    if existing_where is None:
        top_select.set("where", combined)
    else:
        top_select.set("where", exp.And(this=existing_where.copy(), expression=combined))

    new_sql = tree.sql(dialect="mysql", pretty=False)
    n_placeholders = new_sql.count("?")
    new_sql = new_sql.replace("?", "%s")
    return new_sql, n_placeholders


def _validate_password(pwd: str) -> None:
    """Reject placeholder / empty passwords early so the user gets a clear
    error instead of a raw pymysql auth exception (#14)."""
    if not pwd or pwd.strip() == "":
        raise ValueError("请在侧边栏输入 MySQL 密码")
    lowered = pwd.strip().lower()
    placeholders = {"xxxxxxxx", "your_password_here", "changeme", "password", "123456"}
    if lowered in placeholders:
        raise ValueError(
            "检测到占位符密码, 请在侧边栏输入真实的 MySQL 密码"
        )


# ── execute_sql ──
@tool(description="在MySQL数据库中执行SQL查询语句，返回查询结果。file_id 可选: 多文件场景下指定主表对应的上传文件; SQL 里引用其他文件表时, 表名必须与已导入MySQL的表名一致。")
def execute_sql(sql: str, file_id: str = "") -> str:
    """执行 SQL 查询, 返回 JSON 字符串.

    多文件场景:
    - ``file_id`` 显式指定本次查询的主文件 → 用它的脏数据配置 + 表名
    - 不指定时, 如果只加载了一个文件 → 用它
    - 加载了多个文件且不指定 → SQL 里若没明确表名则报「请先调 get_table_schema 选文件」
    """
    files, _, current_table = _snapshot()

    # 1) Resolve which file this query targets
    hit = _get_file(file_id) if file_id else None
    if file_id and not hit:
        return json.dumps({
            "success": False,
            "error": f"file_id={file_id!r} 未找到; 已加载: {list(files.keys())}",
        }, ensure_ascii=False)
    if hit:
        target_file_id, target_entry = hit
        actual_table = target_entry.get("table_name") or target_entry.get("sheets", [{}])[0].get("name", "")
        target_schema = target_entry
    elif len(files) == 1:
        target_file_id, target_entry = next(iter(files.items()))
        actual_table = target_entry.get("table_name") or target_entry.get("sheets", [{}])[0].get("name", "")
        target_schema = target_entry
    else:
        # Multi-file loaded but no file_id given — only safe to proceed if
        # the SQL itself references a known table (real or in EXAMPLE_TABLES).
        # We can't pick a file, so we skip table-name rewriting and dirty
        # injection. The SQL will execute against MySQL directly.
        actual_table = ""
        target_schema = {"sheets": []}

    # 2) Identifier rewrite (table + column aliases) using sqlglot AST
    if actual_table:
        try:
            valid_columns: set[str] = set()
            for sheet in target_schema.get("sheets", []):
                for h in sheet.get("column_headers", []):
                    if h:
                        valid_columns.add(h)
                for cn in sheet.get("column_profiles", {}).keys():
                    if cn:
                        valid_columns.add(cn)
            sql = _rewrite_sql_identifiers(sql, actual_table, valid_columns)
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # 3) Dirty-data WHERE injection
    filters = _collect_dirty_filters(target_schema)
    logger.info(f"[execute_sql] 脏数据过滤: {len(filters)} 列")
    if filters:
        sql, n_placeholders = _inject_dirty_where(sql, filters)
        logger.info(
            f"[execute_sql] 注入后SQL (前200字): {sql[:200]} "
            f"参数位: {n_placeholders}"
        )

    # 4) MySQL connection bootstrap
    mysql = get_mysql()
    if not mysql:
        from utils.config_handler import agent_conf
        from utils.mysql_handler import init_mysql as _init
        cfg = agent_conf.get("mysql", {})
        pwd = cfg.get("password", "")
        try:
            _validate_password(pwd)
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
        try:
            _init(
                cfg.get("host", "127.0.0.1"),
                cfg.get("port", 3306),
                cfg.get("user", "root"),
                str(pwd),
                cfg.get("database", "biagent"),
            )
            mysql = get_mysql()
        except Exception as e:
            return json.dumps({"success": False, "error": f"MySQL未连接: {e}"}, ensure_ascii=False)

    if not mysql:
        return json.dumps({"success": False, "error": "MySQL未连接，请先配置数据库"}, ensure_ascii=False)

    result = mysql.execute_query(sql)
    logger.info(f"[execute_sql] 执行SQL返回 {result.get('row_count', 0)} 行")
    return json.dumps(result, ensure_ascii=False, default=str)
