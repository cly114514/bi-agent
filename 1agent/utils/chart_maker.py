"""
图表生成工具：数据分析图表(matplotlib) + AI生成式配图
"""
import io
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Literal
from PIL import Image

# 动态导入matplotlib，避免未安装时完全崩溃
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False

from utils.logger_handler import logger

# 注册中文字体（黑体/宋体）
_FONT_REGISTERED = False


def _ensure_cn_font():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    _FONT_REGISTERED = True

    if not _HAS_MATPLOTLIB:
        return

    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/truetype/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy-zenhei/wqy-zenhei.ttc",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                fm.fontManager.addfont(fp)
                prop = fm.FontProperties(fname=fp)
                plt.rcParams["font.family"] = prop.get_name()
                plt.rcParams["axes.unicode_minus"] = False
                return
            except Exception:
                continue
    # 回退：让matplotlib用默认字体
    plt.rcParams["axes.unicode_minus"] = False


def _sanitize_label(s: str, max_len: int = 12) -> str:
    s = str(s).strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def render_data_chart(
    data: list[dict],
    chart_type: Literal["bar", "pie", "line"] = "bar",
    title: str = "",
    width: int = 10,
    height: int = 5,
) -> bytes:
    """
    根据查询结果数据生成matplotlib图表，返回PNG字节。
    data: SQL查询返回的字典列表 [{col1: val1, col2: val2}, ...]
    chart_type: "bar" | "pie" | "line"
    """
    if not _HAS_MATPLOTLIB:
        raise RuntimeError("matplotlib未安装，请 pip install matplotlib")

    _ensure_cn_font()

    if not data:
        raise ValueError("数据为空，无法生成图表")

    # 提取列名
    cols = list(data[0].keys())
    if len(cols) < 2:
        raise ValueError("数据列不足2列，无法生成图表")

    labels_col = cols[0]
    values_col = cols[1]

    labels = [_sanitize_label(row[labels_col]) for row in data]
    values = []
    for row in data:
        try:
            values.append(float(row[values_col]))
        except (ValueError, TypeError):
            values.append(0)

    fig, ax = plt.subplots(figsize=(width, height))
    fig.patch.set_facecolor("#f8f9fa")
    ax.set_facecolor("#ffffff")

    if chart_type == "bar":
        bars = ax.bar(labels, values, color="#5B8DEF", edgecolor="#3B6FC4", linewidth=0.8)
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, h, f"{h:.0f}",
                        ha="center", va="bottom", fontsize=8, color="#333333")
        ax.set_xlabel(labels_col, fontsize=10)
        ax.set_ylabel(values_col, fontsize=10)
        ax.set_title(title or f"{values_col} by {labels_col}", fontsize=12, pad=10)
        plt.xticks(rotation=45, ha="right")

    elif chart_type == "pie":
        colors = ["#5B8DEF", "#7B68EE", "#00CED1", "#FF7F50", "#98D8C8",
                  "#F0E68C", "#DDA0DD", "#87CEEB", "#FFB6C1", "#90EE90"]
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90,
               colors=colors[: len(values)], textprops={"fontsize": 8})
        ax.set_title(title or f"{values_col} 占比分布", fontsize=12, pad=10)

    elif chart_type == "line":
        ax.plot(labels, values, marker="o", linewidth=2, markersize=6,
                color="#5B8DEF", markerfacecolor="#ffffff", markeredgecolor="#3B6FC4")
        for i, v in enumerate(values):
            ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8, color="#333333")
        ax.set_xlabel(labels_col, fontsize=10)
        ax.set_ylabel(values_col, fontsize=10)
        ax.set_title(title or f"{values_col} 趋势", fontsize=12, pad=10)
        plt.xticks(rotation=45, ha="right")

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_ai_image(caption: str) -> str:
    """
    调用阿里云通义万相(wanx) API生成配图，返回图片URL。
    如果API未配置或调用失败，返回空字符串。
    """
    try:
        import dashscope
        from dashscope import ImageSynthesis
        from utils.config_handler import agent_conf

        api_key = os.environ.get("DASHSCOPE_API_KEY") or agent_conf.get("dashscope_api_key", "")
        if not api_key:
            logger.warning("[chart_maker] DASHSCOPE_API_KEY 未设置，跳过AI图生成")
            return ""

        model = "wanx2.1-livephotorealm-m-realistic"
        rsp = ImageSynthesis.call(
            model=model,
            prompt=caption,
            api_key=api_key,
        )
        if rsp and rsp.output and rsp.output["image_url"]:
            return rsp.output["image_url"]
        logger.warning(f"[chart_maker] AI图生成失败: {rsp}")
        return ""
    except Exception as e:
        logger.warning(f"[chart_maker] AI图生成异常: {e}")
        return ""