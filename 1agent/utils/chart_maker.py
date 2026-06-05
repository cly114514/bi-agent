"""
图表生成工具 (Plotly engine)

设计目标:
- 每个有 >=2 列且 >=2 行的查询结果都自动渲染一张可交互的 Plotly 图
- 用户可通过 segmented_control 切换 6 种图类型
- 默认按"数据形状 + 数值列数"自动推断可用类型与默认类型
- 时间序列自动按月/季/年聚合, 避免"噪点图"
- 支持中文字体(系统字体栈, 不依赖硬编码路径)
- 适配 Streamlit 明暗主题
- 缺 plotly 时降级为 st.info, 不阻断结果展示
- 双 Y 轴自适应: 数值列量级差距大时自动分主/次坐标
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from utils.type_inference import is_numeric_column, NUMERIC_THRESHOLD

# ── 可选依赖: plotly / pandas ─────────────────────────────────────
try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False
    go = None  # type: ignore[assignment]

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False
    pd = None  # type: ignore[assignment]

# ── 字体与主题 ─────────────────────────────────────────────────────
_CN_FONT_FAMILY = (
    "PingFang SC, Microsoft YaHei, Hiragino Sans GB, "
    "Noto Sans CJK SC, Source Han Sans SC, sans-serif"
)
_DARK_TEMPLATE = "plotly_dark"
_LIGHT_TEMPLATE = "plotly_white"
DEFAULT_COLORS = [
    "#5B8DEF", "#7B68EE", "#00CED1", "#FF7F50", "#98D8C8",
    "#F0E68C", "#DDA0DD", "#87CEEB", "#FFB6C1", "#90EE90",
    "#FFA07A", "#B0C4DE", "#CD853F", "#6A5ACD", "#20B2AA",
]

# ── 形状信息 ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class ShapeInfo:
    all_cols: tuple[str, ...]
    cat_cols: tuple[str, ...]
    numeric_cols: tuple[str, ...]
    date_cols: tuple[str, ...]
    n_rows: int
    n_unique_per_col: dict[str, int] = field(default_factory=dict)
    all_numeric_non_negative: bool = False


# ── 图表选项 ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChartOption:
    id: str
    label: str
    icon: str
    requires: Callable[[ShapeInfo], bool]


def _has_x_axis(s: ShapeInfo) -> bool:
    return bool(s.cat_cols or s.date_cols)


def _has_multi_numeric(s: ShapeInfo) -> bool:
    return len(s.numeric_cols) >= 2


ALL_OPTIONS: tuple[ChartOption, ...] = (
    ChartOption(
        "grouped_bar", "分组柱状图", "📊",
        lambda s: _has_multi_numeric(s) and _has_x_axis(s) and s.n_rows >= 2,
    ),
    ChartOption(
        "multi_line", "多折线", "📈",
        lambda s: _has_multi_numeric(s) and s.n_rows >= 2,
    ),
    ChartOption(
        "stacked_bar", "堆叠柱状图", "📊",
        lambda s: (
            _has_multi_numeric(s) and _has_x_axis(s) and s.n_rows >= 2
            and s.all_numeric_non_negative
        ),
    ),
    ChartOption(
        "stacked_100", "100% 堆叠", "📊",
        lambda s: (
            _has_multi_numeric(s) and _has_x_axis(s) and s.n_rows >= 2
            and s.n_rows <= 12 and s.all_numeric_non_negative
        ),
    ),
    ChartOption(
        "pie", "饼图", "🥧",
        lambda s: (
            len(s.numeric_cols) == 1 and 2 <= s.n_rows <= 8
            and s.all_numeric_non_negative
        ),
    ),
    ChartOption(
        "single_bar", "单系列柱", "📊",
        lambda s: (
            len(s.numeric_cols) == 1 and 2 <= s.n_rows <= 50
        ),
    ),
)
# 日期 X 的数据, 多折线默认优先(更符合时序直觉)
_DEFAULT_PREFERENCE_DATE = ("multi_line", "grouped_bar", "stacked_bar", "single_bar", "stacked_100", "pie")
_DEFAULT_PREFERENCE = ("grouped_bar", "multi_line", "single_bar", "stacked_bar", "stacked_100", "pie")


# ── 视图选项(用户控制) ────────────────────────────────────────────
@dataclass(frozen=True)
class ViewOptions:
    agg_mode: str = "auto"       # "auto" | "aggregated" | "raw"
    y_scale: str = "linear"      # "linear" | "log"
    selected_cols: Optional[tuple[str, ...]] = None  # 限制只画这些数值列


# ── 列类型推断 ─────────────────────────────────────────────────────
_NAME_DATE_HINTS = ("时间", "日期", "月份", "年份", "year", "month", "date", "time", "day", "周")
_DATE_PATTERN = re.compile(r"^\d{2,4}[-/.]\d{1,2}([-/.]\d{1,2})?$")


def _is_date_like(name: str, values: list) -> bool:
    name_l = (name or "").lower()
    if any(kw in name_l for kw in _NAME_DATE_HINTS):
        return True
    if not values:
        return False
    hits = 0
    for v in values:
        if isinstance(v, str) and _DATE_PATTERN.match(v.strip()):
            hits += 1
    return hits >= 0.7 * len(values)


def _is_numeric(values: list) -> bool:
    """#21: delegate to the shared threshold so the chart engine and the
    Excel parser agree on what counts as a numeric column."""
    return is_numeric_column(values, threshold=NUMERIC_THRESHOLD)


def _all_non_negative(values: list) -> bool:
    for v in values:
        if v is None:
            continue
        try:
            if float(v) < 0:
                return False
        except (TypeError, ValueError):
            continue
    return True


def _safe_values(rows: list[dict], col: str) -> list:
    return [r.get(col) for r in rows]


def _coerce_numeric(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _coerce_datetime(v: Any):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if _HAS_PANDAS:
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    return v


def _classify_columns(data: list[dict]) -> ShapeInfo:
    cols = tuple(data[0].keys()) if data else ()
    cat_cols: list[str] = []
    numeric_cols: list[str] = []
    date_cols: list[str] = []
    n_unique: dict[str, int] = {}

    for col in cols:
        values = _safe_values(data, col)
        non_null = [v for v in values if v is not None and str(v).strip() != ""]
        n_unique[col] = len({str(v) for v in non_null})
        if not non_null:
            cat_cols.append(col)
            continue
        if _is_date_like(col, non_null):
            date_cols.append(col)
        elif _is_numeric(non_null):
            numeric_cols.append(col)
        else:
            cat_cols.append(col)

    all_non_neg = all(
        _all_non_negative(_safe_values(data, c)) for c in numeric_cols
    ) if numeric_cols else False

    return ShapeInfo(
        all_cols=cols,
        cat_cols=tuple(cat_cols),
        numeric_cols=tuple(numeric_cols),
        date_cols=tuple(date_cols),
        n_rows=len(data),
        n_unique_per_col=n_unique,
        all_numeric_non_negative=all_non_neg,
    )


def detect_chart_options(data: list[dict]) -> list[ChartOption]:
    """返回对当前数据形状**所有合法**的图表选项, 按默认偏好顺序."""
    if not data or len(data) < 2:
        return []
    cols = list(data[0].keys())
    if len(cols) < 2:
        return []
    shape = _classify_columns(data)
    if not shape.numeric_cols:
        return []
    valid = [opt for opt in ALL_OPTIONS if opt.requires(shape)]
    if not valid:
        return []
    valid_ids = {o.id for o in valid}
    pref = _DEFAULT_PREFERENCE_DATE if shape.date_cols else _DEFAULT_PREFERENCE
    order: list[ChartOption] = []
    for pid in pref:
        for o in valid:
            if o.id == pid and pid in valid_ids:
                order.append(o)
                break
    for o in valid:
        if o not in order:
            order.append(o)
    return order


# ── 时间聚合 ──────────────────────────────────────────────────────
_AGG_THRESHOLD = 30   # 超过这个行数 + X 是日期 -> 自动按月/季/年聚合
_TARGET_BUCKETS = 60  # 目标桶数, 决定用哪个 bucket 粒度

_BUCKET_ORDER = ["day", "week", "month", "quarter", "year"]


def _pick_time_bucket(values: list, target: int = _TARGET_BUCKETS) -> str:
    """根据日期范围自动选 bucket: <30 天→day, <12 周→week, <24 月→month, ..."""
    if not _HAS_PANDAS:
        return "month"
    parsed = pd.to_datetime(pd.Series(values), errors="coerce").dropna()
    if parsed.empty:
        return "month"
    span_days = (parsed.max() - parsed.min()).days
    n_unique = parsed.nunique()
    # 实际需要的桶数 = 唯一日期数(还没聚合)
    # 选使桶数最接近 target 且不超过 target 的 bucket
    candidates = []
    for b in _BUCKET_ORDER:
        if b == "day":
            cap = max(span_days, 1)
        elif b == "week":
            cap = max(span_days // 7, 1)
        elif b == "month":
            cap = max(span_days // 30, 1)
        elif b == "quarter":
            cap = max(span_days // 90, 1)
        else:  # year
            cap = max(span_days // 365, 1)
        # bucket 内平均行数
        avg_per_bucket = n_unique / cap if cap else 0
        # 优先选使桶数 >= 8 且 <= target 的最细粒度
        candidates.append((b, cap, avg_per_bucket))
    # 选最细粒度使得 cap <= target 且 cap >= 4
    for b, cap, _ in candidates:
        if 4 <= cap <= target:
            return b
    # 否则选最接近 target 的
    candidates.sort(key=lambda t: abs(t[1] - target))
    return candidates[0][0]


def _aggregate_time_series(
    data: list[dict],
    x_col: str,
    numeric_cols: list[str],
    bucket: str,
) -> tuple[list[dict], dict]:
    """按时间桶聚合, 对数值列求和. 保留所有原始非数值列(将该桶内的
    不同取值用 ``、`` 拼接, #27).

    Returns (rows, meta) where ``meta`` reports the count of dirty cells
    that were silently coerced to NaN during ``pd.to_numeric`` (#28).
    """
    if not _HAS_PANDAS or not data:
        return data, {"dropped_cells": 0, "dropped_pct": 0.0}
    rows = []
    for r in data:
        v = r.get(x_col)
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            continue
        rows.append({"_ts": ts, **{k: r.get(k) for k in r.keys() if k != x_col}})
    if not rows:
        return [], {"dropped_cells": 0, "dropped_pct": 0.0}
    df = pd.DataFrame(rows)
    # pandas 不同版本 period 频率名不同: 老版 M/Q/Y, 新版 ME/QE/YE
    # 用 Grouper + 显式 freq 字符串, 兼容两种
    try:
        # 新版语法: pd.Grouper with freq like 'ME' / 'QE' / 'YE'
        import pandas as _pd
        _ver = tuple(int(x) for x in _pd.__version__.split(".")[:2])
        if _ver >= (2, 2):
            freq_map = {"day": "D", "week": "W", "month": "ME", "quarter": "QE", "year": "YE"}
        else:
            freq_map = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}
    except Exception:
        freq_map = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}
    freq = freq_map[bucket]
    # 用 Grouper 避开 to_period 的版本差异
    # 先把数值列硬转成数字 (坏 cell -> NaN), 这样 groupby sum 不会因为一个脏 cell 整列挂掉
    dropped_cells = 0
    for c in numeric_cols:
        if c in df.columns:
            before = df[c].notna().sum()
            df[c] = pd.to_numeric(df[c], errors="coerce")
            after = df[c].notna().sum()
            dropped_cells += max(0, before - after)
    total_numeric_cells = max(1, len(df) * max(1, len(numeric_cols)))
    dropped_pct = round(100.0 * dropped_cells / total_numeric_cells, 2)
    agg_map = {c: "sum" for c in numeric_cols if c in df.columns}
    non_numeric = [c for c in df.columns if c not in agg_map and c != "_ts"]

    def _join_unique(series):
        # #27: join all unique non-null values with '、' instead of taking
        # only the first. A time bucket with [A, B, A] now reads as
        # "A、B" rather than "A" (which used to silently lose the B row).
        vals = sorted({str(v) for v in series if v is not None and str(v).strip() != ""})
        return "、".join(vals) if vals else None

    if non_numeric:
        agg_map.update({c: _join_unique for c in non_numeric})
    grouped = df.groupby(pd.Grouper(key="_ts", freq=freq)).agg(agg_map).reset_index()
    grouped = grouped.rename(columns={"_ts": x_col})
    # 把 x_col 改回普通 timestamp(去掉 freq 信息), 便于 plotly 解析
    grouped[x_col] = pd.to_datetime(grouped[x_col])
    out = []
    for _, row in grouped.iterrows():
        rec = {x_col: row[x_col]}
        for c in numeric_cols:
            v = row[c]
            rec[c] = float(v) if pd.notna(v) else 0.0
        for c in non_numeric:
            rec[c] = row[c]
        out.append(rec)
    return out, {"dropped_cells": int(dropped_cells), "dropped_pct": dropped_pct}


def _should_aggregate(shape: ShapeInfo, agg_mode: str) -> bool:
    if agg_mode == "raw":
        return False
    if agg_mode == "aggregated":
        return bool(shape.date_cols)
    # auto: 日期 X 且行数多
    return bool(shape.date_cols) and shape.n_rows > _AGG_THRESHOLD


# ── 量级/离群检测 ────────────────────────────────────────────────
def _col_max(values: list) -> float:
    mx = 0.0
    for v in values:
        f = _coerce_numeric(v)
        if f is not None and f > mx:
            mx = f
    return mx


def _col_median(values: list) -> float:
    nums = sorted(
        f for f in (_coerce_numeric(v) for v in values) if f is not None
    )
    if not nums:
        return 0.0
    n = len(nums)
    if n % 2:
        return nums[n // 2]
    return (nums[n // 2 - 1] + nums[n // 2]) / 2


def _needs_log_scale(data: list[dict], numeric_cols: list[str]) -> bool:
    """当任一数值列 max/median > 50 且 max > 0, 建议对数刻度."""
    for c in numeric_cols:
        vals = _safe_values(data, c)
        mx = _col_max(vals)
        med = _col_median(vals)
        if med <= 0:
            med = 1.0
        if mx > 0 and mx / med > 50:
            return True
    return False


# ── 双 Y 轴自适应 ────────────────────────────────────────────────
def _split_dual_y(numeric_cols: list[str], data: list[dict]) -> Optional[int]:
    """如果数值列量级差距大(max 比 > 100), 把"金额/价格/费用"放左, 数量/计数/率放右(次坐标)."""
    if len(numeric_cols) < 2 or not data:
        return None
    maxes = [_col_max(_safe_values(data, c)) for c in numeric_cols]
    if not maxes or maxes[0] <= 0:
        return None
    positives = [m for m in maxes if m > 0]
    if len(positives) < 2:
        return None
    mx_max = max(positives)
    mx_min = min(positives)
    if mx_max / max(mx_min, 1.0) < 100:
        return None
    # 按列名启发: 数量/计数/ID/百分比/率放次坐标, 其他放主坐标
    secondary_hints = ("数量", "计数", "次数", "count", "qty", "id", "率", "percent", "%")
    split = len(numeric_cols)
    for i, c in enumerate(numeric_cols):
        if any(kw in c.lower() for kw in secondary_hints):
            split = i
            break
    if split == 0 or split == len(numeric_cols):
        return None
    return split


# ── 数据预处理辅助 ────────────────────────────────────────────────
def _pick_x_col(shape: ShapeInfo) -> Optional[str]:
    if shape.date_cols:
        return shape.date_cols[0]
    if shape.cat_cols:
        return shape.cat_cols[0]
    return None


def _maybe_promote_dates(values: list, name: str) -> list:
    if not _HAS_PANDAS:
        return values
    if not any(kw in (name or "").lower() for kw in _NAME_DATE_HINTS):
        return values
    try:
        parsed = pd.to_datetime(values, errors="coerce")
        if parsed.notna().sum() >= 0.7 * max(1, len(values)):
            return list(parsed)
    except Exception:
        return values
    return values


def _cap_categorical_x(
    data: list[dict],
    x_col: str,
    numeric_cols: list[str],
    cap: int = 30,
) -> list[dict]:
    """非日期 X 唯一值过多时, 按第一个数值列求和取 top-cap, 其余合并为 '其他'."""
    if len(data) <= cap or not numeric_cols:
        return data[:cap] if len(data) > cap else data
    primary = numeric_cols[0]
    enriched = []
    for r in data:
        v = _coerce_numeric(r.get(primary))
        enriched.append((r, v if v is not None else 0.0))
    enriched.sort(key=lambda t: t[1], reverse=True)
    top = [r for r, _ in enriched[:cap]]
    rest_total = sum(v for _, v in enriched[cap:])
    if rest_total > 0 and top:
        agg_row = {c: None for c in data[0].keys()}
        agg_row[x_col] = "其他"
        agg_row[primary] = round(rest_total, 2)
        top.append(agg_row)
    return top


# ── 颜色板 ────────────────────────────────────────────────────────
def _color_for(i: int) -> str:
    return DEFAULT_COLORS[i % len(DEFAULT_COLORS)]


# ── 图表构建器 ────────────────────────────────────────────────────
def _resolve_template() -> str:
    try:
        import streamlit as st
        base = st.get_option("theme.base")
        if base == "dark":
            return _DARK_TEMPLATE
    except Exception:
        pass
    return _LIGHT_TEMPLATE


def _apply_common_layout(fig, title: str, y_scale: str = "linear") -> None:
    fig.update_layout(
        title=title or None,
        template=_resolve_template(),
        font=dict(family=_CN_FONT_FAMILY, size=12),
        margin=dict(l=50, r=20, t=40 if title else 20, b=90),
        # 图例放底部, 不与右上 modebar 撞
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0),
        hovermode="x unified",
    )
    if y_scale == "log":
        # log 模式: 关闭 unified hover(数字跨度过大 hover 不友好), 改 y 类型
        fig.update_layout(hovermode="closest")
        fig.update_yaxes(type="log")


def _build_grouped_bar(data, shape, x_col, title, view: ViewOptions, dual_split):
    fig = go.Figure()
    cols_to_plot = view.selected_cols or tuple(shape.numeric_cols)
    primary = cols_to_plot[:dual_split] if dual_split else cols_to_plot
    secondary = cols_to_plot[dual_split:] if dual_split else ()
    for i, y_col in enumerate(primary):
        x_vals = [r.get(x_col) for r in data]
        y_vals = [_coerce_numeric(r.get(y_col)) for r in data]
        fig.add_trace(go.Bar(
            x=x_vals, y=y_vals, name=y_col,
            marker_color=_color_for(i),
        ))
    for j, y_col in enumerate(secondary):
        x_vals = [r.get(x_col) for r in data]
        y_vals = [_coerce_numeric(r.get(y_col)) for r in data]
        fig.add_trace(go.Bar(
            x=x_vals, y=y_vals, name=y_col,
            marker_color=_color_for(len(primary) + j),
            yaxis="y2",
        ))
    if secondary:
        fig.update_layout(barmode="group", yaxis2=dict(overlaying="y", side="right", title=""))
    else:
        fig.update_layout(barmode="group")
    fig.update_xaxes(tickangle=-30)
    _apply_common_layout(fig, title, view.y_scale)
    return fig


def _build_multi_line(data, shape, x_col, title, view: ViewOptions, dual_split):
    fig = go.Figure()
    cols_to_plot = view.selected_cols or tuple(shape.numeric_cols)
    primary = cols_to_plot[:dual_split] if dual_split else cols_to_plot
    secondary = cols_to_plot[dual_split:] if dual_split else ()
    for i, y_col in enumerate(primary):
        x_vals = [r.get(x_col) for r in data]
        y_vals = [_coerce_numeric(r.get(y_col)) for r in data]
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals, name=y_col, mode="lines+markers",
            line=dict(color=_color_for(i), width=2),
            marker=dict(size=6),
        ))
    for j, y_col in enumerate(secondary):
        x_vals = [r.get(x_col) for r in data]
        y_vals = [_coerce_numeric(r.get(y_col)) for r in data]
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals, name=y_col, mode="lines+markers",
            line=dict(color=_color_for(len(primary) + j), width=2, dash="dot"),
            marker=dict(size=6), yaxis="y2",
        ))
    if secondary:
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", title=""))
    # 标注最大/最小点(仅主坐标)
    if primary and data:
        try:
            first_col = primary[0]
            ys = [_coerce_numeric(r.get(first_col)) for r in data]
            xs = [r.get(x_col) for r in data]
            valid = [(x, y) for x, y in zip(xs, ys) if y is not None]
            if valid:
                max_pt = max(valid, key=lambda t: t[1])
                min_pt = min(valid, key=lambda t: t[1])
                fig.add_annotation(
                    x=max_pt[0], y=max_pt[1], text=f"▲ {max_pt[1]:,.0f}",
                    showarrow=True, arrowhead=2, ax=0, ay=-30,
                    bgcolor="rgba(91,141,239,0.85)", font=dict(color="white", size=10),
                )
                if min_pt[0] != max_pt[0]:
                    fig.add_annotation(
                        x=min_pt[0], y=min_pt[1], text=f"▼ {min_pt[1]:,.0f}",
                        showarrow=True, arrowhead=2, ax=0, ay=30,
                        bgcolor="rgba(150,150,150,0.7)", font=dict(color="white", size=10),
                    )
        except Exception:
            pass
    fig.update_xaxes(tickangle=-30)
    _apply_common_layout(fig, title, view.y_scale)
    return fig


def _build_stacked_bar(data, shape, x_col, title, view: ViewOptions, percent: bool = False):
    fig = go.Figure()
    cols_to_plot = view.selected_cols or tuple(shape.numeric_cols)
    for i, y_col in enumerate(cols_to_plot):
        x_vals = [r.get(x_col) for r in data]
        y_vals = [_coerce_numeric(r.get(y_col)) for r in data]
        fig.add_trace(go.Bar(
            x=x_vals, y=y_vals, name=y_col,
            marker_color=_color_for(i),
        ))
    fig.update_layout(
        barmode="relative" if percent else "stack",
        barnorm="percent" if percent else None,
    )
    fig.update_xaxes(tickangle=-30)
    _apply_common_layout(fig, title, view.y_scale)
    return fig


def _build_pie(data, shape, x_col, title, view: ViewOptions):
    y_col = shape.numeric_cols[0]
    labels = [str(r.get(x_col, "")) for r in data]
    values = [_coerce_numeric(r.get(y_col)) or 0.0 for r in data]
    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        marker=dict(colors=DEFAULT_COLORS[: len(labels)]),
        textinfo="label+percent",
        textposition="auto",
        hovertemplate="<b>%{label}</b><br>值: %{value}<br>占比: %{percent}<extra></extra>",
    )])
    _apply_common_layout(fig, title, view.y_scale)
    return fig


def _build_single_bar(data, shape, x_col, title, view: ViewOptions):
    y_col = shape.numeric_cols[0]
    x_vals = [r.get(x_col) for r in data]
    y_vals = [_coerce_numeric(r.get(y_col)) for r in data]
    fig = go.Figure(data=[go.Bar(
        x=x_vals, y=y_vals,
        marker=dict(color=_color_for(0), line=dict(color="#3B6FC4", width=0.8)),
        text=[f"{v:,.0f}" if v is not None else "" for v in y_vals],
        textposition="outside",
    )])
    fig.update_xaxes(tickangle=-30)
    _apply_common_layout(fig, title, view.y_scale)
    return fig


def build_figure(
    data: list[dict],
    option: ChartOption,
    title: str = "",
    view: Optional[ViewOptions] = None,
) -> tuple[Any, dict]:
    """构造一张 Plotly Figure. 返回 (figure, meta), meta 描述聚合情况给 UI 显示."""
    if not _HAS_PLOTLY:
        raise RuntimeError("Plotly 未安装")
    if not data or len(data) < 2:
        raise ValueError("数据为空或行数不足(需要 >=2 行)")
    cols = list(data[0].keys())
    if len(cols) < 2:
        raise ValueError("数据列数不足(需要 >=2 列)")

    view = view or ViewOptions()
    shape = _classify_columns(data)
    x_col = _pick_x_col(shape)
    if x_col is None:
        raise ValueError("找不到可作 X 轴的列(分类或日期列)")

    work = [dict(r) for r in data]

    # X 列日期提升
    if x_col in shape.date_cols and _HAS_PANDAS:
        x_values = _safe_values(work, x_col)
        promoted = _maybe_promote_dates(x_values, x_col)
        for r, v in zip(work, promoted):
            r[x_col] = v

    # 时间序列自动聚合
    meta = {"aggregated": False, "bucket": None, "original_rows": len(work), "buckets": len(work)}
    if x_col in shape.date_cols and _should_aggregate(shape, view.agg_mode):
        bucket = _pick_time_bucket(_safe_values(work, x_col))
        n_before = len(work)
        work, agg_meta = _aggregate_time_series(
            work, x_col,
            [c for c in (view.selected_cols or shape.numeric_cols)],
            bucket,
        )
        meta.update({"aggregated": True, "bucket": bucket,
                     "original_rows": n_before, "buckets": len(work),
                     # #28: surface how many cells got coerced to NaN
                     # so the UI can warn the user about lost rows.
                     "dropped_cells": agg_meta.get("dropped_cells", 0),
                     "dropped_pct": agg_meta.get("dropped_pct", 0.0)})
    elif option.id == "single_bar" and shape.n_unique_per_col.get(x_col, 0) > 200:
        work = _cap_categorical_x(work, x_col, list(shape.numeric_cols), cap=200)
    elif option.id in ("grouped_bar", "multi_line") and not (x_col in shape.date_cols):
        # 非日期 X 且唯一值 > 30: top-30 + 其他
        n_unique = shape.n_unique_per_col.get(x_col, 0)
        if n_unique > 30:
            work = _cap_categorical_x(work, x_col, list(shape.numeric_cols), cap=30)

    # 重新分类(聚合后 shape 可能变化)
    if meta["aggregated"]:
        shape = _classify_columns(work)
        x_col = _pick_x_col(shape) or x_col

    # 限制数值列(用户选择的)
    cols_to_plot = list(view.selected_cols) if view.selected_cols else list(shape.numeric_cols)

    # 双 Y 轴
    dual_split = _split_dual_y(cols_to_plot, work) if option.id in ("grouped_bar", "multi_line") else None

    if option.id == "grouped_bar":
        fig = _build_grouped_bar(work, shape, x_col, title, view, dual_split)
    elif option.id == "multi_line":
        fig = _build_multi_line(work, shape, x_col, title, view, dual_split)
    elif option.id == "stacked_bar":
        fig = _build_stacked_bar(work, shape, x_col, title, view, percent=False)
    elif option.id == "stacked_100":
        fig = _build_stacked_bar(work, shape, x_col, title, view, percent=True)
    elif option.id == "pie":
        fig = _build_pie(work, shape, x_col, title, view)
    elif option.id == "single_bar":
        fig = _build_single_bar(work, shape, x_col, title, view)
    else:
        raise ValueError(f"未知图表类型: {option.id}")
    return fig, meta


def get_figure_html(fig, include_plotlyjs: str = "cdn") -> str:
    if not _HAS_PLOTLY or fig is None:
        return ""
    return fig.to_html(include_plotlyjs=include_plotlyjs, full_html=False)


# ── Streamlit 渲染入口 ────────────────────────────────────────────
_AGG_LABELS = {"auto": "自动", "aggregated": "按月聚合", "raw": "原始"}
_YSCALE_LABELS = {"linear": "线性", "log": "对数"}


def _segmented(st, label: str, options: list[str], default: str, key: str) -> str:
    """用 segmented_control (>=1.40) 或 radio (回退) 让用户选."""
    if hasattr(st, "segmented_control"):
        return st.segmented_control(
            label, options=options, default=default,
            key=key, label_visibility="collapsed",
        )
    try:
        idx = options.index(default)
    except ValueError:
        idx = 0
    return st.radio(
        label, options=options, index=idx, horizontal=True,
        key=key, label_visibility="collapsed",
    )


def render_chart(
    data: list[dict],
    msg_id: str,
    title: str = "",
) -> None:
    if not _HAS_PLOTLY:
        try:
            import streamlit as st
            st.info("Plotly 未安装, 跳过图表渲染 (pip install plotly)")
        except Exception:
            pass
        return
    if not data or len(data) < 2 or len(data[0].keys()) < 2:
        return

    try:
        import streamlit as st
    except ImportError:
        return

    # 形状/可用类型
    shape = _classify_columns(data)
    options = detect_chart_options(data)
    if not options:
        st.caption("⚠️ 当前数据形状没有可用的图表类型(需要至少 1 个分类/日期列 + 1 个数值列)")
        return

    # 持久化全部 UI 选择
    chart_state = st.session_state.setdefault("chart_state", {})
    state = chart_state.get(msg_id, {})
    selected_id = state.get("type", options[0].id)
    if selected_id not in {o.id for o in options}:
        selected_id = options[0].id
    agg_mode = state.get("agg", "auto")
    y_scale = state.get("yscale", "linear")
    selected_cols = state.get("cols")  # None 或 tuple

    # ── 图表类型 segmented_control ──
    type_labels = [o.label for o in options]
    type_ids = [o.id for o in options]
    chosen_type = _segmented(
        st, "图表类型", type_labels,
        options[next(i for i, o in enumerate(options) if o.id == selected_id)].label,
        key=f"ct_{msg_id}",
    )
    if chosen_type:
        new_id = type_ids[type_labels.index(chosen_type)]
        if new_id != selected_id:
            chart_state.setdefault(msg_id, {})["type"] = new_id
            st.rerun()
    selected_id = chart_state.get(msg_id, {}).get("type", selected_id)

    # ── 数据视图 segmented_control ──
    can_agg = bool(shape.date_cols)
    if can_agg:
        agg_options = ["自动", "按月聚合", "原始"]
        default_label = "自动" if agg_mode == "auto" else ("按月聚合" if agg_mode == "aggregated" else "原始")
        chosen_agg = _segmented(
            st, "数据视图", agg_options, default_label,
            key=f"agg_{msg_id}",
        )
        new_agg = {"自动": "auto", "按月聚合": "aggregated", "原始": "raw"}.get(chosen_agg, agg_mode)
        if new_agg != agg_mode:
            chart_state.setdefault(msg_id, {})["agg"] = new_agg
            st.rerun()
        agg_mode = chart_state.get(msg_id, {}).get("agg", agg_mode)
    else:
        agg_mode = "raw"

    # ── Y 轴 segmented_control ──
    show_log = _needs_log_scale(data, list(shape.numeric_cols))
    yscale_options = ["线性", "对数"] if show_log or y_scale == "log" else ["线性"]
    default_ys = "对数" if y_scale == "log" else "线性"
    chosen_ys = _segmented(
        st, "Y 轴", yscale_options, default_ys,
        key=f"ys_{msg_id}",
    )
    new_ys = "log" if chosen_ys == "对数" else "linear"
    if new_ys != y_scale:
        chart_state.setdefault(msg_id, {})["yscale"] = new_ys
        st.rerun()
    y_scale = chart_state.get(msg_id, {}).get("yscale", y_scale)

    # ── 数值列选择(>3 个时显示) ──
    if len(shape.numeric_cols) > 3:
        chosen_cols = st.multiselect(
            "要画的数值列", options=list(shape.numeric_cols),
            default=list(selected_cols) if selected_cols else list(shape.numeric_cols[:5]),
            key=f"cols_{msg_id}",
        )
        if set(chosen_cols) != set(selected_cols or ()):
            chart_state.setdefault(msg_id, {})["cols"] = tuple(chosen_cols) if chosen_cols else None
            st.rerun()
        selected_cols = chart_state.get(msg_id, {}).get("cols")
        if not selected_cols:
            selected_cols = tuple(shape.numeric_cols[:5])

    # ── 渲染 ──
    view = ViewOptions(agg_mode=agg_mode, y_scale=y_scale,
                       selected_cols=tuple(selected_cols) if selected_cols else None)
    option = next(o for o in options if o.id == selected_id)
    try:
        fig, meta = build_figure(data, option, title=title, view=view)
    except Exception as e:
        st.error(f"图表生成失败: {e}")
        return

    # 提示聚合信息
    if meta.get("aggregated"):
        # #28: surface dirty cells the aggregation dropped so the user
        # knows their data wasn't silently corrupted.
        dropped_msg = ""
        dropped = meta.get("dropped_cells", 0)
        if dropped and dropped > 0:
            pct = meta.get("dropped_pct", 0.0)
            dropped_msg = f"  |  ⚠️ 已排除 {dropped} 个无法解析的脏值({pct}%)"
        st.caption(
            f"📦 已按 **{meta['bucket']}** 聚合: {meta['original_rows']} 行 → {meta['buckets']} 个桶. "
            f"切到「原始」看明细{dropped_msg}"
        )

    st.plotly_chart(
        fig,
        use_container_width=True,
        theme=None,
        config={
            "displaylogo": False,
            "displayModeBar": "hover",
            "toImageButtonOptions": {"format": "png", "scale": 2, "filename": "bi_chart"},
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
        key=f"chart_{msg_id}_{selected_id}_{agg_mode}_{y_scale}",
    )


# ── 调试入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = [
        {"月份": "2025-01", "销售额": 120000, "成本": 80000, "利润": 40000},
        {"月份": "2025-02", "销售额": 135000, "成本": 85000, "利润": 50000},
        {"月份": "2025-03", "销售额": 98000,  "成本": 70000, "利润": 28000},
        {"月份": "2025-04", "销售额": 156000, "成本": 90000, "利润": 66000},
    ]
    opts = detect_chart_options(sample)
    print("可用类型:", [o.id for o in opts])
    print("默认:", opts[0].id if opts else "无")
    for o in opts:
        fig, meta = build_figure(sample, o, title=f"测试 {o.label}")
        print(f"  {o.id} -> traces={len(fig.data)}, meta={meta}")

    # 668 行时间序列测试
    print("\n=== 668 行时间序列 (auto agg) ===")
    import random
    random.seed(42)
    big = []
    for i in range(668):
        big.append({
            "签约时间": pd.Timestamp("2013-01-01") + pd.Timedelta(days=random.randint(0, 1800)),
            "总金额": random.randint(0, 40_000_000),
            "购买数量": random.randint(1, 50),
            "产品": random.choice(["A", "B", "C"]),
        })
    for o in opts[:3]:
        fig, meta = build_figure(big, o, view=ViewOptions(agg_mode="auto"))
        print(f"  {o.id} -> traces={len(fig.data)}, aggregated={meta.get('aggregated')}, "
              f"{meta.get('original_rows')}行 → {meta.get('buckets')}桶({meta.get('bucket')})")
