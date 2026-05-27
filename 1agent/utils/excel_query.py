"""
Excel行级查询引擎：无MySQL时直接查询Excel数据。
支持过滤、分组聚合、排序、限制。
"""
import json
import re
from typing import Any, Optional


def _to_rows_dict(headers: list[str], raw_rows: list[list]) -> list[dict]:
    """将Excel原始行转为字典列表"""
    result = []
    for row in raw_rows:
        d = {}
        for i, h in enumerate(headers):
            if h:
                d[h] = row[i] if i < len(row) else None
        result.append(d)
    return result


def _try_numeric(v: Any) -> Any:
    """尝试转数值"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    # 处理中文数字
    cn_num_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for cn, num in cn_num_map.items():
        if s.startswith(cn):
            try:
                return float(s.replace(cn, "").replace("万", "0000").replace("元", ""))
            except:
                pass
    # 去掉人民币符号等
    s = re.sub(r"[¥￥$,，]", "", s)
    try:
        if "." in s:
            return float(s)
        return int(s)
    except:
        return v


def _matches_condition(value: Any, cond_op: str, cond_val: Any) -> bool:
    """判断单行是否匹配条件"""
    num_val = _try_numeric(value)
    num_cond = _try_numeric(cond_val)

    if cond_op == "=":
        return str(value).strip() == str(cond_val).strip()
    elif cond_op == "!=":
        return str(value).strip() != str(cond_val).strip()
    elif cond_op == ">":
        return num_val is not None and num_cond is not None and num_val > num_cond
    elif cond_op == ">=":
        return num_val is not None and num_cond is not None and num_val >= num_cond
    elif cond_op == "<":
        return num_val is not None and num_cond is not None and num_val < num_cond
    elif cond_op == "<=":
        return num_val is not None and num_cond is not None and num_val <= num_cond
    elif cond_op == "contains":
        return str(cond_val) in str(value)
    elif cond_op == "in":
        return value in (cond_val if isinstance(cond_val, list) else [cond_val])
    return True


def _parse_conditions(query: str) -> list[dict]:
    """从自然语言中解析过滤条件"""
    conditions = []
    # 匹配 "区域=华东" / "销售额>1000" / "产品包含手机"
    pattern = r"([\u4e00-\u9fff\w]+)\s*(=|!=|>|<|>=|<=|contains|in)\s*([^\s，,]+)"
    for m in re.finditer(pattern, query):
        col, op, val = m.groups()
        conditions.append({"column": col.strip(), "operator": op, "value": val.strip()})
    return conditions


def _parse_aggregation(query: str) -> dict:
    """从查询语句解析分组聚合意图"""
    agg_map = {
        "求和": "sum", "总计": "sum", "总额": "sum", "汇总": "sum",
        "平均": "avg", "均值": "avg", "平均值": "avg",
        "计数": "count", "个数": "count", "数量": "count",
        "最大": "max", "最高": "max", "最多": "max",
        "最小": "min", "最低": "min", "最少": "min",
    }
    for keyword, agg in agg_map.items():
        if keyword in query:
            return {"agg": agg}
    return {}


def _find_value_column(query: str, headers: list[str]) -> Optional[str]:
    """找查询中最可能是数值的列"""
    # 优先找金额/数量/销售额等关键词（避免被分组列优先匹配）
    value_keywords = ["额", "量", "数", "值", "率", "价", "费", "本", "润", "金额", "销售", "利润", "成本"]
    for h in headers:
        for kw in value_keywords:
            if kw in h:
                return h
    # 其次找明确提到的列名
    for h in headers:
        if h in query:
            return h
    return headers[1] if len(headers) > 1 else None


def _find_group_column(query: str, headers: list[str]) -> Optional[str]:
    """找查询中最可能是分组的列（分类维度）"""
    # 优先：看用户查询中明确提到了哪个列名
    for h in headers:
        if h in query:
            return h
    # 其次：按关键词匹配
    group_keywords = ["区域", "地区", "类别", "分类", "产品", "城市", "渠道", "部门", "月份", "年", "季度", "月"]
    for h in headers:
        for kw in group_keywords:
            if kw in h:
                return h
    return headers[0] if headers else None


def execute_excel_query(
    parsed: dict,
    query: str,
) -> dict:
    """
    执行Excel查询，返回与execute_sql相同的JSON格式。
    parsed: parse_excel_bytes()返回的字典
    query: 自然语言查询语句
    """
    try:
        sheets = parsed.get("sheets", [])
        if not sheets:
            return {"success": False, "error": "Excel数据为空"}

        sheet = sheets[0]
        headers = [h for h in sheet.get("column_headers", []) if h]
        raw_rows = sheet.get("raw_rows", [])

        if not headers or not raw_rows:
            return {"success": False, "error": "Excel数据缺少表头或行数据"}

        rows_dict = _to_rows_dict(headers, raw_rows)

        # 1. 解析过滤条件
        conditions = _parse_conditions(query)
        filtered = rows_dict
        for cond in conditions:
            col = cond["column"]
            op = cond["operator"]
            val = cond["value"]
            filtered = [r for r in filtered if _matches_condition(r.get(col), op, val)]

        # 2. 检查是否有分组聚合需求
        agg_info = _parse_aggregation(query)
        has_group = any(kw in query for kw in ["各", "每", "按", "分组", "类别", "区域"])

        if agg_info and has_group:
            # 分组聚合
            group_col = _find_group_column(query, headers)
            value_col = _find_value_column(query, headers) or headers[1] if len(headers) > 1 else None
            agg = agg_info["agg"]

            if group_col and value_col:
                groups: dict = {}
                for r in filtered:
                    key = str(r.get(group_col, "未知"))
                    val = _try_numeric(r.get(value_col)) or 0
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(val)

                result = []
                for key, vals in groups.items():
                    if agg == "sum":
                        agg_val = sum(vals)
                    elif agg == "avg":
                        agg_val = sum(vals) / len(vals) if vals else 0
                    elif agg == "count":
                        agg_val = len(vals)
                    elif agg == "max":
                        agg_val = max(vals)
                    elif agg == "min":
                        agg_val = min(vals)
                    else:
                        agg_val = sum(vals)
                    result.append({group_col: key, value_col: round(agg_val, 2)})

                # 排序
                if "高" in query or "大" in query or "多" in query:
                    result.sort(key=lambda x: _try_numeric(x.get(value_col)) or 0, reverse=True)
                elif "低" in query or "小" in query or "少" in query:
                    result.sort(key=lambda x: _try_numeric(x.get(value_col)) or 0)
                else:
                    result.sort(key=lambda x: _try_numeric(x.get(value_col)) or 0, reverse=True)

                # limit
                limit_match = re.search(r"前(\d+)|top(\d+)|limit(\d+)", query)
                if limit_match:
                    limit = int(limit_match.group(1) or limit_match.group(2) or limit_match.group(3))
                    result = result[:limit]
                else:
                    result = result[:20]

                return {"success": True, "data": result, "row_count": len(result)}

        # 3. 普通过滤/排序
        # 排序
        if "高" in query or "大" in query or "多" in query:
            value_col = _find_value_column(query, headers)
            if value_col:
                filtered.sort(key=lambda x: _try_numeric(x.get(value_col)) or 0, reverse=True)
        elif "低" in query or "小" in query or "少" in query:
            value_col = _find_value_column(query, headers)
            if value_col:
                filtered.sort(key=lambda x: _try_numeric(x.get(value_col)) or 0)

        # limit
        limit_match = re.search(r"前(\d+)|top(\d+)|limit(\d+)", query)
        if limit_match:
            limit = int(limit_match.group(1) or limit_match.group(2) or limit_match.group(3))
            filtered = filtered[:limit]

        return {"success": True, "data": filtered, "row_count": len(filtered)}

    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # 简单测试
    sample = {
        "sheets": [{
            "name": "Sheet1",
            "rows": 5,
            "columns": 3,
            "column_headers": ["区域", "产品", "销售额"],
            "raw_rows": [
                ["华东", "产品A", 125000],
                ["华东", "产品B", 98000],
                ["华南", "产品A", 156000],
                ["华南", "产品B", 104000],
                ["华北", "产品A", 210000],
            ]
        }]
    }

    test_queries = [
        "各区域销售额求和",
        "各产品销售额汇总前3",
        "华东地区销售额",
    ]

    for q in test_queries:
        print(f"\n查询: {q}")
        print(json.dumps(execute_excel_query(sample, q), ensure_ascii=False, indent=2))