import json
import re
from langchain_core.tools import tool
from rag.rag_service import RagSummarizeService
from utils.mysql_handler import get_mysql
from utils.logger_handler import logger

rag = None  # lazy init

def _get_rag():
    global rag
    if rag is None:
        rag = RagSummarizeService()
    return rag
_current_schema: dict = {}
_current_table_name: str = ""


def set_schema(schema: dict):
    global _current_schema
    _current_schema = schema


def set_table_name(name: str):
    global _current_table_name
    _current_table_name = name


@tool(description="生成SQL查询语句：根据用户需求和上传的Excel数据表字段，从向量库检索相似的SQL示例，生成精准的SQL查询语句")
def generate_sql(query: str) -> str:
    return _get_rag().rag_summarize(query)


@tool(description="执行查询（无MySQL时直接查Excel数据）")
def execute_excel(query: str) -> str:
    """当MySQL不可用时，直接在上传的Excel数据上执行查询"""
    if _current_schema and _current_schema.get("sheets"):
        from utils.excel_query import execute_excel_query
        result = execute_excel_query(_current_schema, query)
        return json.dumps(result, ensure_ascii=False, default=str)
    return json.dumps({"success": False, "error": "尚未上传Excel数据"})


@tool(description="获取当前已上传数据表的字段结构信息，包含字段名、数据类型、所有唯一值和脏数据标记")
def get_table_schema() -> str:
    if not _current_schema:
        return "尚未上传数据表，请先上传Excel文件"
    lines = []
    for sheet in _current_schema.get("sheets", []):
        lines.append(f"Sheet: {sheet['name']} ({sheet['rows']}行 x {sheet['columns']}列)")
        headers = [h for h in sheet["column_headers"] if h]
        lines.append(f"字段: {', '.join(headers)}")
        for col_name, profile in sheet.get("column_profiles", {}).items():
            dtype_str = profile["dtype"]
            if dtype_str == "numeric" and "min" in profile:
                dtype_str += f"(范围{profile['min']}~{profile['max']})"
            samples = profile.get("sample_values", [])
            if samples:
                all_vals = " | ".join(samples)
                dtype_str += f" 所有唯一值=[{all_vals}]"
            dirty = profile.get("dirty_values", [])
            if dirty:
                dtype_str += f" ⚠️脏数据={dirty}"
            lines.append(f"  {col_name}: {dtype_str}")
    return "\n".join(lines)


@tool(description="在MySQL数据库中执行SQL查询语句，返回查询结果。入参sql为完整的SQL语句，出参为JSON格式的查询结果")
def execute_sql(sql: str) -> str:
    # 自动替换 RAG 示例中的表名为实际 MySQL 表名
    example_tables = [
        "contracts", "sales", "revenue", "orders", "users", "合同", "Sheet1",
        "employees", "inventory", "tickets", "returns",
        "daily_orders", "ads", "monthly_target", "preorders",
        "user_retention", "logistics", "purchase_orders",
        "marketing_campaign", "projects", "equipment_logs",
        "budget_tracking", "user_login", "sales_compare",
        "profit_month", "user_activity", "stores",
    ]
    actual_table = _current_table_name or (
        _current_schema.get("sheets", [{}])[0].get("name", "") if _current_schema else ""
    )
    if actual_table:
        # Validate table name (allow only safe chars)
        import re as _re
        if _re.match(r'^[\w\u4e00-\u9fff]+$', actual_table):
            pattern = r'\b(?:' + '|'.join(example_tables) + r')\b'
            sql = _re.sub(pattern, actual_table, sql, flags=_re.IGNORECASE)

    # 自动替换 RAG 常见英文列名为实际中文列名
    col_map = {}
    for sheet in _current_schema.get("sheets", []):
        for h in sheet.get("column_headers", []):
            if h:
                col_map[h] = h
        for cn, profile in sheet.get("column_profiles", {}).items():
            col_map[cn] = cn
    column_aliases = {
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
    for eng, cn in column_aliases.items():
        if cn in col_map:
            sql = re.sub(r'\b' + eng + r'\b', f"`{cn}`", sql, flags=re.IGNORECASE)

    # 自动注入脏数据过滤 WHERE 条件
    extra_conditions = []
    for sheet in _current_schema.get("sheets", []):
        for col_name, profile in sheet.get("column_profiles", {}).items():
            dirty = profile.get("dirty_values", [])
            if not dirty:
                continue
            col_ref = f"`{col_name}`"
            str_dirty = [d for d in dirty if not (d.replace(".", "").replace("-", "").isdigit() or (d.startswith("-") and d[1:].replace(".", "").isdigit()))]
            if str_dirty:
                vals = ", ".join(f"'{d}'" for d in str_dirty[:10])
                extra_conditions.append(f"{col_ref} NOT IN ({vals})")
            num_dirty = [d for d in dirty if d not in str_dirty]
            num_zeros = [d for d in num_dirty if d in ("0", "0.0", "0.00")]
            if num_zeros:
                extra_conditions.append(f"{col_ref} > 0")

    logger.info(f"[execute_sql] 脏数据过滤: {extra_conditions if extra_conditions else '无'}")

    if extra_conditions:
        where_clause = " AND ".join(extra_conditions)
        # 注入到 SQL 中：在 ORDER BY / LIMIT / GROUP BY 之前插入 WHERE
        injection_point = len(sql)
        for kw in ["ORDER BY", "LIMIT", "GROUP BY", "HAVING"]:
            m = re.search(r'\b' + kw + r'\b', sql, re.IGNORECASE)
            if m:
                injection_point = min(injection_point, m.start())
        if "WHERE" not in sql.upper():
            sql = sql[:injection_point] + f" WHERE {where_clause} " + sql[injection_point:]
        else:
            sql = sql[:injection_point] + f" AND {where_clause} " + sql[injection_point:]
        logger.info(f"[execute_sql] 注入后SQL: {sql[:200]}")

    mysql = get_mysql()
    if not mysql:
        from utils.config_handler import agent_conf
        from utils.mysql_handler import init_mysql as _init
        cfg = agent_conf.get("mysql", {})
        try:
            _init(cfg["host"], cfg["port"], cfg["user"], str(cfg["password"]), cfg["database"])
            mysql = get_mysql()
        except Exception as e:
            return json.dumps({"success": False, "error": f"MySQL未连接: {e}"}, ensure_ascii=False)
    if not mysql:
        return json.dumps({"success": False, "error": "MySQL未连接，请先配置数据库"}, ensure_ascii=False)
    result = mysql.execute_query(sql)
    logger.info(f"[execute_sql] 执行SQL返回 {result.get('row_count', 0)} 行")
    return json.dumps(result, ensure_ascii=False, default=str)
