from __future__ import annotations
import statistics
import re
from pathlib import Path
from typing import Any
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


_NUMERIC_FIELD_KEYWORDS = ["金额", "价格", "数量", "收入", "成本", "利润", "单价", "总额", "费用", "薪资"]
_DATE_FIELD_KEYWORDS = ["时间", "日期", "年份", "月份"]
_BOOL_FIELD_KEYWORDS = ["是否", "有无", "是非"]
_DATE_PATTERN = re.compile(r"^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}")


def _is_numeric_field(col_name: str) -> bool:
    return any(kw in col_name for kw in _NUMERIC_FIELD_KEYWORDS)


def _is_date_field(col_name: str) -> bool:
    return any(kw in col_name for kw in _DATE_FIELD_KEYWORDS)


def _is_bool_field(col_name: str) -> bool:
    return any(kw in col_name for kw in _BOOL_FIELD_KEYWORDS)


def _is_pure_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _detect_dirty_values(values: list, col_name: str, dtype: str) -> list[str]:
    dirty = set()
    if not values:
        return []
    cleaned = [str(v).strip() for v in values if v is not None and str(v).strip()]

    # 占位符/空值始终是脏数据
    for v in cleaned:
        if v.lower() in ("null", "n/a", "na", "无", "测试", "test"):
            dirty.add(v)

    # 字段语义检测
    if _is_numeric_field(col_name):
        for v in cleaned:
            if not _is_pure_number(v):
                dirty.add(v)
            elif float(v) <= 0:
                dirty.add(v)
    if _is_date_field(col_name):
        for v in cleaned:
            if len(v) > 5 and not _DATE_PATTERN.match(v):
                dirty.add(v)
    if _is_bool_field(col_name):
        for v in cleaned:
            if v not in ("是", "否", "0", "1", "true", "false", "True", "False", "TRUE", "FALSE"):
                dirty.add(v)

    # 同列模式对比：检测不合群的异常值
    if len(cleaned) >= 3:
        # 统计列中各类字符模式的比例
        chinese_count = sum(1 for v in cleaned if any('\u4e00' <= c <= '\u9fff' for c in v))
        pure_digit_count = sum(1 for v in cleaned if v.isdigit())
        alnum_count = sum(1 for v in cleaned if v.replace("-", "").isalnum() and not v.isdigit() and not any('\u4e00' <= c <= '\u9fff' for c in v))
        total = len(cleaned)

        # 如果 ≥70% 的值包含中文 → 纯数字值视为脏数据
        if chinese_count / total >= 0.7:
            for v in cleaned:
                if v.isdigit() and len(v) >= 3:
                    dirty.add(v)
        # 如果 ≥70% 是纯数字 → 包含中文的值视为脏数据
        elif pure_digit_count / total >= 0.7:
            for v in cleaned:
                if any('\u4e00' <= c <= '\u9fff' for c in v):
                    dirty.add(v)

    return sorted(dirty)


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _infer_dtype(samples: list) -> str:
    numeric = 0
    for s in samples:
        if s is None:
            continue
        try:
            float(s)
            numeric += 1
        except (ValueError, TypeError):
            pass
    if numeric > len(samples) * 0.7:
        return "numeric"
    return "string"


def _column_profile(values: list, col_name: str = "") -> dict:
    cleaned = [_safe_str(v) for v in values if _safe_str(v) is not None]
    if not cleaned:
        return {"dtype": "empty", "count": 0, "unique_values": 0, "sample_values": []}
    dtype = _infer_dtype(cleaned)
    unique = sorted(set(cleaned))
    numeric_vals = []
    for v in cleaned:
        try:
            numeric_vals.append(float(v))
        except (ValueError, TypeError):
            pass
    profile: dict = {
        "dtype": dtype,
        "count": len(cleaned),
        "unique_values": len(unique),
        "sample_values": unique[:20],
        "dirty_values": _detect_dirty_values(cleaned, col_name, dtype),
    }
    if dtype == "numeric" and numeric_vals:
        profile["min"] = round(min(numeric_vals), 2)
        profile["max"] = round(max(numeric_vals), 2)
        profile["mean"] = round(statistics.mean(numeric_vals), 2)
    return profile


def parse_excel_bytes(data: bytes, filename: str) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        wb = load_workbook(tmp_path, data_only=True, read_only=True)
        result = {"filename": filename, "sheets": []}

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(min_row=1, values_only=True))

            def row_is_empty(r):
                return all(_safe_str(c) is None for c in r)
            rows = [r for r in rows if not row_is_empty(r)]
            if not rows:
                continue

            max_cols = max(len(r) for r in rows)
            cols = [[] for _ in range(max_cols)]
            for r in rows:
                for i in range(max_cols):
                    cols[i].append(r[i] if i < len(r) else None)

            column_headers = []
            for c in cols:
                col_header = _safe_str(c[0]) if c else None
                column_headers.append(col_header)

            row_headers = []
            for r in rows:
                rh = _safe_str(r[0]) if r else None
                row_headers.append(rh)

            column_profiles = {}
            for i, c in enumerate(cols):
                header_name = column_headers[i] or f"列{get_column_letter(i + 1)}"
                body = c[1:]
                profile = _column_profile(body, header_name or "")
                profile["column_letter"] = get_column_letter(i + 1)
                profile["column_index"] = i
                column_profiles[header_name] = profile

            result["sheets"].append({
                "name": sheet_name,
                "rows": len(rows),
                "columns": max_cols,
                "column_headers": column_headers,
                "row_headers": row_headers,
                "column_profiles": column_profiles,
                "raw_rows": [[_safe_str(c) for c in r] for r in rows[1:]],
            })

        wb.close()
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def format_schema_for_prompt(parsed: dict) -> str:
    lines = []
    for sheet in parsed["sheets"]:
        headers = [h for h in sheet["column_headers"] if h]
        lines.append(f"表[{sheet['name']}]({sheet['rows']}行): {', '.join(headers)}")
    return "\n".join(lines)
