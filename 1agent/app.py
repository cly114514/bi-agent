from __future__ import annotations
import os
import sys
from pathlib import Path
# 不管从哪个目录启动 streamlit, 都把本文件所在目录放到 sys.path 最前面,
# 保证 `from utils.x import y` / `from agent.x import y` 始终能解析(项目无 __init__.py, 是 namespace package).
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 显式加载 repo 根目录的 .env(不依赖 streamlit 的 cwd)
try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass

os.environ.setdefault("OPENAI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
import json
import re
import uuid
import streamlit as st
from agent.react_agent import ReactAgent
from agent.tools.agent_tools import set_files, set_table_name
from utils.excel_parser import parse_excel_bytes, format_schema_for_prompt
from utils.mysql_handler import init_mysql, get_mysql, _friendly_mysql_error
from utils.chart_maker import render_chart
from utils.config_handler import agent_conf

st.set_page_config(page_title="BI Agent 看板智能体", layout="wide")
st.title("BI Agent 看板智能体")
st.caption("上传多个 Excel 数据文件，用自然语言生成 SQL 查询并生成图表 (支持多文件 JOIN + 20 轮上下文记忆)")

# ── Constants ──
MAX_CACHED_RESULTS = 50
MAX_HISTORY = 20  # 上下文记忆轮数
MAX_UPLOAD_FILES = 5

# ── MySQL 初始化 ──
if "mysql_ready" not in st.session_state:
    st.session_state["mysql_ready"] = False
    st.session_state["mysql_error"] = ""

if "mysql_password" not in st.session_state:
    st.session_state["mysql_password"] = ""


def _try_connect_mysql(password: str):
    mysql_cfg = agent_conf.get("mysql", {})
    try:
        init_mysql(
            host=mysql_cfg.get("host", "127.0.0.1"),
            port=mysql_cfg.get("port", 3306),
            user=mysql_cfg.get("user", "root"),
            password=password,
            database=mysql_cfg.get("database", "biagent"),
        )
        st.session_state["mysql_ready"] = True
        st.session_state["mysql_error"] = ""
    except Exception as e:
        st.session_state["mysql_ready"] = False
        # #30: show a sanitized error to the user, raw error in logger
        st.session_state["mysql_error"] = _friendly_mysql_error(e)

# ── 初始化 session_state ──
if "agent" not in st.session_state:
    with st.spinner("正在初始化 Agent..."):
        st.session_state["agent"] = ReactAgent()

for key, default in [
    ("message", []),
    # Multi-file state. ``excel_files`` is a dict keyed by file_id
    # (filename by default). Each value is the parsed Excel dict plus
    # ``"id"`` and ``"table_name"`` keys set after MySQL import.
    ("excel_files", {}),
    # Backwards-compat: first file's schema text used in the prompt
    ("excel_schema_text", ""),
    ("pending_clarify", None),
    # ``imported_files`` maps file_id -> list of imported MySQL table
    # names (one per sheet).
    ("imported_files", {}),
    ("query_results", {}),
    # Chat history (user / assistant turns only, capped at MAX_HISTORY)
    ("chat_history", []),
    ("chart_selection", {}),
    # Per-file (file_id, sheet_name) → bytes for cached uploads
    ("cached_file_bytes", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── 辅助函数 ──
CLARIFY_PATTERN = re.compile(r"\[CLARIFY\]\s*(.*?)\s*\[/CLARIFY\]", re.DOTALL)


def _find_json_in_text(text: str) -> str | None:
    """在文本中定位完整的 JSON 字符串（处理嵌套 + 优先匹配 ``{"success":`` 锚点）"""
    anchor = re.search(r'\{"success"\s*:', text)
    starts = []
    if anchor:
        starts.append(anchor.start())
    last = text.rfind("{")
    if last >= 0 and (not starts or last != starts[0]):
        starts.append(last)

    for start in starts:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def extract_exec_result(text: str) -> dict | None:
    json_str = _find_json_in_text(text)
    if not json_str:
        return None
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    if "success" in data and ("data" in data or "error" in data):
        return data
    return None


def extract_clarify(text: str) -> tuple[str, dict | None]:
    m = CLARIFY_PATTERN.search(text)
    if not m:
        return text, None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return text, None
    clean = CLARIFY_PATTERN.sub("", text).strip()
    return clean, data


def _all_known_field_names() -> set[str]:
    """Union of all column headers across all loaded files (for
    strip_schema_content)."""
    out: set[str] = set()
    for f in st.session_state.get("excel_files", {}).values():
        for sheet in f.get("sheets", []):
            for h in sheet.get("column_headers", []):
                if h:
                    out.add(h)
    return out


def strip_schema_content(text: str) -> str:
    if not text:
        return text
    field_names = _all_known_field_names()
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("Sheet") and ("行" in s or "列" in s):
            continue
        if s.startswith("字段:") or s.startswith("字段列表:"):
            continue
        if s.startswith("数据文件:"):
            continue
        if s.startswith("当前已加载"):
            continue
        if s.startswith("- file_id="):
            continue
        if any(s.startswith(fn + ":") or s.startswith(fn + "：") for fn in field_names):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)
    for marker in ["[上传的数据表结构]", "[用户问题]"]:
        text = text.replace(marker, "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cache_result(results: dict, msg_id: str, data: list):
    results[msg_id] = data
    if len(results) > MAX_CACHED_RESULTS:
        oldest = next(iter(results))
        del results[oldest]
        for key in ("chart_selection", "chart_state"):
            store = st.session_state.get(key)
            if isinstance(store, dict):
                store.pop(oldest, None)


def _new_result_id() -> str:
    return f"result_{uuid.uuid4().hex[:8]}"


def _extract_original_query(text: str) -> str:
    m = re.search(r"\[原问题\]\s*(.*?)$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def extract_sql_only(text: str) -> str:
    """#22: prefer markdown code blocks before falling back to rfind."""
    fenced = re.search(r"```(?:sql|SQL)?\s*\n?(.*?)```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate:
            return candidate
    for kw in ("SELECT", "INSERT INTO", "UPDATE", "DELETE FROM"):
        idx = text.upper().rfind(kw)
        if idx >= 0:
            return text[idx:].strip()
    return text.strip()


def _build_merged_schema_text() -> str:
    """Concatenate schema summaries for all loaded files. Used as the
    prompt prefix so the LLM sees one consistent view of all available
    data."""
    excel_files = st.session_state.get("excel_files", {})
    if not excel_files:
        return ""
    parts = []
    for fid, f in excel_files.items():
        filename = f.get("filename", "?")
        table = f.get("table_name", "(未导入MySQL)")
        parts.append(
            f"文件: {filename} (file_id={fid!r}, MySQL表名={table})\n"
            f"{format_schema_for_prompt(f)}"
        )
    return "\n\n".join(parts)


def _append_history(role: str, content: str) -> None:
    """Append one turn to the chat history and trim to MAX_HISTORY."""
    if not content:
        return
    history = st.session_state.get("chat_history", [])
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY:
        # Keep the most-recent MAX_HISTORY messages
        st.session_state["chat_history"] = history[-MAX_HISTORY:]
    else:
        st.session_state["chat_history"] = history


def _clear_history() -> None:
    """Reset the chat history (UI messages + LLM history)."""
    st.session_state["message"] = []
    st.session_state["chat_history"] = []
    st.session_state["pending_clarify"] = None
    st.session_state["query_results"] = {}


# ── 图表渲染辅助 ──
def _show_data_with_chart(prompt: str, data: list, msg_id: str, msg_text: str) -> None:
    st.chat_message("assistant").write(msg_text)
    st.dataframe(data, use_container_width=True)
    render_chart(data, msg_id, title=prompt)


# ── 侧边栏：文件上传 + 导入MySQL + 上下文管理 ──
with st.sidebar:
    st.header("数据源")

    # MySQL 连接
    if st.session_state.get("mysql_ready"):
        st.success("MySQL 已连接")
    else:
        with st.expander("🔌 MySQL 连接", expanded=not st.session_state.get("mysql_ready")):
            mysql_cfg = agent_conf.get("mysql", {})
            pwd = st.text_input("MySQL 密码", type="password",
                                value=st.session_state.get("mysql_password", ""),
                                key="mysql_pwd_input")
            st.caption(f"连接: {mysql_cfg.get('user','root')}@{mysql_cfg.get('host','127.0.0.1')}:{mysql_cfg.get('port',3306)}/{mysql_cfg.get('database','biagent')}")
            if st.button("🔗 连接 MySQL"):
                st.session_state["mysql_password"] = pwd
                _try_connect_mysql(pwd)
                st.rerun()
            if st.session_state.get("mysql_error"):
                st.error(st.session_state["mysql_error"])

    # Multi-file uploader
    uploaded_files = st.file_uploader(
        "上传 Excel 文件 (可多选)",
        type=["xlsx", "xls"],
        help=f"支持 .xlsx/.xls 格式, 最多 {MAX_UPLOAD_FILES} 个文件, 自动提取表头字段; 多文件可做 JOIN",
        accept_multiple_files=True,
    )

    if uploaded_files:
        if len(uploaded_files) > MAX_UPLOAD_FILES:
            st.warning(f"最多 {MAX_UPLOAD_FILES} 个文件, 已截断到前 {MAX_UPLOAD_FILES} 个")
            uploaded_files = uploaded_files[:MAX_UPLOAD_FILES]

        # Cache bytes per file (re-parse only when bytes change)
        for f in uploaded_files:
            fid = f.name
            content = f.read()
            if content:
                # Re-parse if we don't have it cached or bytes changed
                cached = st.session_state["cached_file_bytes"].get(fid)
                if cached != content:
                    parsed = parse_excel_bytes(content, fid)
                    parsed["id"] = fid
                    st.session_state["excel_files"][fid] = parsed
                    st.session_state["cached_file_bytes"][fid] = content

    excel_files = st.session_state.get("excel_files", {})
    if excel_files:
        st.success(f"已加载 {len(excel_files)} 个文件")
        # Push the multi-file state to the tools module so the
        # tool's module-level _current_files is in sync.
        set_files(list(excel_files.values()))
        # Update the prompt schema text
        st.session_state["excel_schema_text"] = _build_merged_schema_text()

        for fid, f in excel_files.items():
            with st.expander(f"📄 {f.get('filename', '?')} (id={fid!r}, {len(f.get('sheets', []))} sheets)"):
                for sheet in f.get("sheets", []):
                    st.write(
                        f"**Sheet: {sheet['name']}** ({sheet['rows']}行 × {sheet['columns']}列)"
                    )
                    headers = [h for h in sheet.get("column_headers", []) if h]
                    st.caption(f"字段 ({len(headers)}个): {', '.join(headers)}")
                    for col_name, profile in sheet.get("column_profiles", {}).items():
                        if profile.get("dtype") == "empty":
                            continue
                        dtype_str = profile["dtype"]
                        if profile.get("sample_values"):
                            samples = "、".join(profile["sample_values"][:3])
                            dtype_str += f"  |  示例: {samples}"
                        dirty = profile.get("dirty_values", [])
                        if dirty:
                            dtype_str += f"  |  ⚠️脏: {', '.join(dirty[:5])}"
                        st.caption(f"  `{col_name}` → {dtype_str}")

        # 导入MySQL按钮
        mysql = get_mysql()
        if mysql and excel_files:
            already_imported = bool(st.session_state.get("imported_files"))
            if not already_imported:
                if st.button("📥 导入 MySQL 数据库 (所有文件)", type="primary"):
                    with st.spinner("正在导入..."):
                        for fid, f in excel_files.items():
                            imported_tables: list[str] = []
                            for sheet in f.get("sheets", []):
                                headers = [h or f"col_{i}" for i, h in enumerate(sheet.get("column_headers", []))]
                                sample_rows = sheet.get("raw_rows", [])[:10]
                                table_name = mysql.create_table_from_excel(
                                    sheet["name"], headers, sample_rows
                                )
                                raw_rows = sheet.get("raw_rows", [])
                                if raw_rows:
                                    mysql.insert_rows(table_name, headers, raw_rows)
                                imported_tables.append(table_name)
                            # Persist the table name on the file entry so
                            # tools know which MySQL table to rewrite to.
                            first_table = imported_tables[0] if imported_tables else ""
                            f["table_name"] = first_table
                            st.session_state["excel_files"][fid] = f
                            st.session_state["imported_files"][fid] = imported_tables
                            if fid == next(iter(excel_files)):
                                set_table_name(first_table)
                        set_files(list(st.session_state["excel_files"].values()))
                    st.rerun()

        if st.session_state.get("imported_files"):
            with st.expander("✅ 已入库表", expanded=False):
                for fid, tables in st.session_state["imported_files"].items():
                    st.write(f"**{fid}**: {', '.join(f'`{t}`' for t in tables)}")

    # ── 上下文管理 ──
    st.divider()
    st.subheader("对话上下文")
    history = st.session_state.get("chat_history", [])
    st.caption(f"已记忆 {len(history)} / {MAX_HISTORY} 轮")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🗑 清除上下文", use_container_width=True,
                     help="清空聊天历史和对话 UI, 不动数据文件"):
            _clear_history()
            st.rerun()
    with col_b:
        if st.button("♻️ 全部重置", use_container_width=True,
                     help="清空上下文 + 删除所有 MySQL 表 + 清空上传文件"):
            mysql = get_mysql()
            if mysql:
                for tables in st.session_state.get("imported_files", {}).values():
                    for t in tables:
                        try:
                            mysql._connect().cursor().execute(f"DROP TABLE IF EXISTS `{t}`")
                        except Exception:
                            pass
            _clear_history()
            st.session_state["excel_files"] = {}
            st.session_state["excel_schema_text"] = ""
            st.session_state["imported_files"] = {}
            st.session_state["cached_file_bytes"] = {}
            set_files([])
            st.rerun()


# ── 对话区 ──
for msg in st.session_state["message"]:
    content = msg["content"]
    role = msg["role"]
    if role == "assistant":
        content = strip_schema_content(content)
    if not content:
        continue
    st.chat_message(role).write(content)
    result_id = msg.get("result_id", "")
    if result_id and result_id in st.session_state.get("query_results", {}):
        cached_data = st.session_state["query_results"][result_id]
        st.dataframe(cached_data, use_container_width=True)
        render_chart(cached_data, result_id, title=content)

# ── 未决澄清 ──
pending = st.session_state.get("pending_clarify")
if pending:
    st.chat_message("assistant").write(pending["message"])
    options = {s["label"]: s["value"] for s in pending["suggestions"]}
    choice = st.radio(
        pending.get("ambiguous_term", "请选择"),
        list(options.keys()),
        key=f"clarify_{pending.get('ambiguous_term', 'x')}",
        captions=[s.get("desc", "") for s in pending["suggestions"]],
    )
    if st.button("确认选择", type="primary"):
        clarify_msg = (
            f"[用户选择] 将「{pending['ambiguous_term']}」映射为字段「{options[choice]}」\n"
            f"[原问题] {pending['original_query']}"
        )
        st.session_state["message"].append({"role": "user", "content": clarify_msg})
        st.session_state["pending_clarify"] = None
        st.rerun()


def _last_user_msg_needs_processing():
    msgs = st.session_state.get("message", [])
    if not msgs:
        return False, None
    last = msgs[-1]
    if last["role"] != "user":
        return False, None
    return True, last["content"]


# ── 输入框 ──
if not pending:
    needs_process, auto_prompt = _last_user_msg_needs_processing()
    if needs_process and auto_prompt:
        prompt = auto_prompt
    else:
        prompt = st.chat_input("输入分析需求，如：查询华东地区的销售总额")

    if prompt:
        if not needs_process:
            st.chat_message("user").write(prompt)
            st.session_state["message"].append({"role": "user", "content": prompt})
            # Also append to chat history (the LLM will see this on the
            # next turn too, so we add it now).
            _append_history("user", prompt)

        full_query = prompt
        schema_text = st.session_state.get("excel_schema_text", "")
        if schema_text:
            full_query = f"[上传的数据表结构]\n{schema_text}\n\n[用户问题]\n{prompt}"

        # Pass prior chat history to the LLM (excluding the current
        # user turn which was just appended). The agent caps it
        # internally as a safety net.
        history_for_llm = list(st.session_state.get("chat_history", []))[:-1]

        response_chunks = []
        with st.spinner("思考中..."):
            res_stream = st.session_state["agent"].execute_stream(
                full_query, history=history_for_llm,
            )
            for chunk in res_stream:
                response_chunks.append(chunk)
        full_response = "".join(response_chunks)

        clean_text, clarify_data = extract_clarify(full_response)

        if clarify_data:
            clean_text = strip_schema_content(clean_text) if clean_text else ""
            if clean_text:
                st.chat_message("assistant").write(clean_text)
                st.session_state["message"].append({"role": "assistant", "content": clean_text})
                _append_history("assistant", clean_text)
            clarify_data["original_query"] = _extract_original_query(prompt)
            st.session_state["pending_clarify"] = clarify_data
            st.rerun()
        else:
            exec_result = extract_exec_result(full_response)
            if exec_result and exec_result.get("success"):
                row_count = exec_result.get("row_count", 0)
                data = exec_result.get("data", [])
                msg = f"查询完成，返回 {row_count} 行数据"
                msg_id = _new_result_id()
                _cache_result(st.session_state["query_results"], msg_id, data)
                _show_data_with_chart(prompt, data, msg_id, msg)
                st.session_state["message"].append({
                    "role": "assistant", "content": msg, "result_id": msg_id,
                })
                _append_history("assistant", msg)
            elif exec_result and exec_result.get("error"):
                err_msg = exec_result["error"]
                mysql_down = (
                    "MySQL未连接" in err_msg
                    or "Connection refused" in err_msg
                    or "Can't connect" in err_msg
                    or "Access denied" in err_msg
                    or "Unknown database" in err_msg
                )
                if mysql_down and st.session_state.get("excel_files"):
                    # Fallback to Excel direct query (single-file only)
                    from utils.excel_query import execute_excel_query
                    first_file = next(iter(st.session_state["excel_files"].values()))
                    excel_result = execute_excel_query(first_file, prompt)
                    if excel_result.get("success"):
                        data = excel_result.get("data", [])
                        rc = excel_result.get("row_count", 0)
                        msg = f"MySQL 不可用,已改用 Excel 直查,返回 {rc} 行数据"
                        mid = _new_result_id()
                        _cache_result(st.session_state["query_results"], mid, data)
                        _show_data_with_chart(prompt, data, mid, msg)
                        st.session_state["message"].append({
                            "role": "assistant", "content": msg, "result_id": mid,
                        })
                        _append_history("assistant", msg)
                    else:
                        st.chat_message("assistant").write(
                            f"无法执行查询: {excel_result.get('error', '未知')}"
                        )
                        st.session_state["message"].append({
                            "role": "assistant", "content": f"错误: {err_msg}",
                        })
                        _append_history("assistant", f"错误: {err_msg}")
                else:
                    st.chat_message("assistant").error(err_msg)
                    st.session_state["message"].append({
                        "role": "assistant", "content": f"错误：{err_msg}",
                    })
                    _append_history("assistant", f"错误：{err_msg}")
            else:
                content = strip_schema_content(full_response)
                sql_only = extract_sql_only(full_response)
                has_sql = bool(re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", sql_only, re.IGNORECASE))
                if has_sql:
                    import json as _json
                    from agent.tools.agent_tools import execute_sql as _exec
                    result_json = _exec.func(sql_only)
                    try:
                        result = _json.loads(result_json)
                    except Exception:
                        result = None
                    if result and result.get("success"):
                        data = result.get("data", [])
                        rc = result.get("row_count", 0)
                        msg = f"查询完成，返回 {rc} 行数据"
                        mid = _new_result_id()
                        _cache_result(st.session_state["query_results"], mid, data)
                        _show_data_with_chart(prompt, data, mid, msg)
                        st.session_state["message"].append({
                            "role": "assistant", "content": msg, "result_id": mid,
                        })
                        _append_history("assistant", msg)
                    else:
                        st.chat_message("assistant").error(f"SQL执行失败: {result.get('error', result) if result else '未知'}")
                        st.session_state["message"].append({"role": "assistant", "content": "查询失败"})
                        _append_history("assistant", "查询失败")
                elif st.session_state.get("excel_files"):
                    from utils.excel_query import execute_excel_query
                    first_file = next(iter(st.session_state["excel_files"].values()))
                    excel_result = execute_excel_query(first_file, prompt)
                    if excel_result.get("success"):
                        data = excel_result.get("data", [])
                        rc = excel_result.get("row_count", 0)
                        msg = f"查询完成，返回 {rc} 行数据"
                        mid = _new_result_id()
                        _cache_result(st.session_state["query_results"], mid, data)
                        _show_data_with_chart(prompt, data, mid, msg)
                        st.session_state["message"].append({
                            "role": "assistant", "content": msg, "result_id": mid,
                        })
                        _append_history("assistant", msg)
                    else:
                        st.chat_message("assistant").write(f"无法执行查询: {excel_result.get('error', '未知')}")
                        st.session_state["message"].append({"role": "assistant", "content": content})
                        _append_history("assistant", content)
                else:
                    st.chat_message("assistant").write(content)
                    st.session_state["message"].append({"role": "assistant", "content": content})
                    _append_history("assistant", content)
            if needs_process:
                st.rerun()
