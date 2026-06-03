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
    """当前版本不检测脏数据，假设数据已清洗"""
    return []


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
            # Forward-fill：合并单元格导致的空白自动补全为上一行同列的值
            for i in range(max_cols):
                last_non_empty = None
                for r_idx in range(len(rows)):
                    if i < len(rows[r_idx]) and _safe_str(rows[r_idx][i]) is not None:
                        last_non_empty = rows[r_idx][i]
                    elif last_non_empty is not None:
                        rows[r_idx] = list(rows[r_idx])
                        while len(rows[r_idx]) <= i:
                            rows[r_idx].append(None)
                        rows[r_idx][i] = last_non_empty

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
