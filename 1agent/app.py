import json
import re
import streamlit as st
from agent.react_agent import ReactAgent
from agent.tools.agent_tools import set_schema, set_table_name
from utils.excel_parser import parse_excel_bytes, format_schema_for_prompt
from utils.mysql_handler import init_mysql, get_mysql
from utils.config_handler import agent_conf
from utils.chart_maker import render_chart

st.set_page_config(page_title="BI Agent 看板智能体", layout="wide")
st.title("BI Agent 看板智能体")
st.caption("上传 Excel 数据文件，用自然语言生成 SQL 查询并生成图表")

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
        st.session_state["mysql_error"] = f"{type(e).__name__}: {e}"

# ── 初始化 session_state ──
if "agent" not in st.session_state:
    with st.spinner("正在初始化 Agent..."):
        st.session_state["agent"] = ReactAgent()

for key, default in [
    ("message", []), ("excel_parsed", None), ("excel_parsed_list", []),
    ("excel_schema_text", ""),
    ("pending_clarify", None), ("table_imported", False), ("mysql_table", ""),
    ("query_results", {}),
    ("chart_selection", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── 辅助函数 ──
CLARIFY_PATTERN = re.compile(r"\[CLARIFY\]\s*(.*?)\s*\[/CLARIFY\]", re.DOTALL)


def _find_json_in_text(text: str) -> str | None:
    """在文本中定位完整的 JSON 字符串（处理嵌套）"""
    start = text.find("{")
    while start >= 0:
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
        start = text.find("{", start + 1)
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


def strip_schema_content(text: str) -> str:
    if not text:
        return text
    field_names = set()
    for parsed in st.session_state.get("excel_parsed_list", []):
        for sheet in parsed.get("sheets", []):
            for h in sheet.get("column_headers", []):
                if h:
                    field_names.add(h)
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
        if any(s.startswith(fn + ":") or s.startswith(fn + "：") for fn in field_names):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)
    for marker in ["[上传的数据表结构]", "[用户问题]"]:
        text = text.replace(marker, "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_original_query(text: str) -> str:
    m = re.search(r"\[原问题\]\s*(.*?)$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def extract_sql_only(text: str) -> str:
    for kw in ("SELECT", "INSERT INTO", "UPDATE", "DELETE FROM"):
        idx = text.upper().rfind(kw)
        if idx >= 0:
            return text[idx:].strip()
    return text.strip()
    all_fields = set()
    for sheet in parsed.get("sheets", []):
        for h in sheet.get("column_headers", []):
            if h:
                all_fields.add(h)
    if not all_fields:
        return query

    for field in sorted(all_fields, key=lambda x: -len(x)):
        # 用户说了该字段的子串
        if field not in query:
            # 提取字段的纯中文核心部分（去掉括号等）
            core = re.split(r'[（(]', field)[0]
            if len(core) >= 2 and core in query and core != field:
                query = query.replace(core, field)
    return query


# ── 侧边栏：文件上传 + 导入MySQL ──
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

    uploaded_files = st.file_uploader(
        "上传 Excel 文件", type=["xlsx", "xls"],
        help="支持 .xlsx 格式，最多 5 个文件，自动提取表头字段",
        accept_multiple_files=True,
    )

    if uploaded_files:
        parsed_list = []
        schema_parts = []
        file_names = []
        for f in uploaded_files:
            file_bytes = f.read()
            if file_bytes:
                parsed = parse_excel_bytes(file_bytes, f.name)
                parsed_list.append(parsed)
                schema_parts.append(format_schema_for_prompt(parsed))
                file_names.append(f.name)

        if parsed_list:
            # 只有文件变更时才重置导入状态
            old_names = st.session_state.get("_uploaded_names", [])
            if sorted(file_names) != sorted(old_names):
                st.session_state["table_imported"] = False
                st.session_state["mysql_table"] = ""
                st.session_state["_uploaded_names"] = file_names
            st.session_state["excel_parsed_list"] = parsed_list
            st.session_state["excel_parsed"] = parsed_list[0]
            st.session_state["excel_schema_text"] = "\n\n".join(schema_parts)
            # 为每个 sheet 打上真实表名标记
            for parsed in parsed_list:
                base = parsed["filename"].rsplit(".", 1)[0]
                for s in parsed["sheets"]:
                    s["_mysql_table"] = f"{base}_{s['name']}"
            set_schema({"sheets": sum((p["sheets"] for p in parsed_list), [])})

    parsed_list = st.session_state.get("excel_parsed_list", [])
    if parsed_list:
        for parsed in parsed_list:
            st.success(f"已加载: {parsed['filename']}")
            for sheet in parsed["sheets"]:
                with st.expander(f"{parsed['filename']} / {sheet['name']} ({sheet['rows']}行 × {sheet['columns']}列)"):
                    headers = [h for h in sheet["column_headers"] if h]
                    st.write(f"**字段 ({len(headers)}个):** {', '.join(headers)}")
                    for col_name, profile in sheet["column_profiles"].items():
                        if profile["dtype"] == "empty":
                            continue
                        dtype_str = profile["dtype"]
                        if profile["sample_values"]:
                            samples = "、".join(profile["sample_values"][:3])
                            dtype_str += f"  |  示例: {samples}"
                        dirty = profile.get("dirty_values", [])
                        if dirty:
                            dtype_str += f"  |  ⚠️脏: {', '.join(dirty[:5])}"
                        st.caption(f"`{col_name}` → {dtype_str}")

        # 导入MySQL按钮
        mysql = get_mysql()
        parsed_list = st.session_state.get("excel_parsed_list", [])
        if mysql and parsed_list and not st.session_state.get("table_imported"):
            if st.button("📥 导入 MySQL 数据库", type="primary"):
                # 确保数据库存在（可能在别处被删了）
                mysql._ensure_db()
                with st.spinner("正在导入..."):
                    table_names = []
                    for parsed in parsed_list:
                        for sheet in parsed["sheets"]:
                            headers = [h or f"col_{i}" for i, h in enumerate(sheet["column_headers"])]
                            sample_rows = sheet.get("raw_rows", [])[:10]
                            # 表名 = 文件名_Sheet名，避免不同文件的同名 Sheet 冲突
                            base_name = parsed["filename"].rsplit(".", 1)[0]
                            table_key = f"{base_name}_{sheet['name']}"
                            table_name = mysql.create_table_from_excel(
                                table_key, headers, sample_rows
                            )
                            raw_rows = sheet.get("raw_rows", [])
                            if raw_rows:
                                mysql.insert_rows(table_name, headers, raw_rows)
                            table_names.append(table_name)
                if table_names:
                    st.session_state["table_imported"] = True
                    st.session_state["mysql_table"] = ", ".join(table_names)
                    set_table_name(table_names[0] if table_names else "")
                else:
                    st.error("导入失败：没有可导入的数据")
                st.rerun()

        if st.session_state.get("table_imported"):
            st.success(f"已入库: `{st.session_state['mysql_table']}`")

    if st.button("🗑 清除上下文"):
        st.session_state["message"] = []
        st.session_state["pending_clarify"] = None
        st.session_state["query_results"] = {}
        st.session_state["agent"].clear_history()
        st.rerun()



# ── 对话区 ──
for msg in st.session_state["message"]:
    content = msg["content"]
    role = msg["role"]
    if role == "assistant":
        content = strip_schema_content(content)
    if not content:
        continue
    # 所有assistant消息用小号字体（数据表除外）
    is_result = "查询完成" in content and len(content) < 30
    if role == "assistant" and not is_result:
        st.chat_message(role).markdown(f"<small>{content}</small>", unsafe_allow_html=True)
    else:
        st.chat_message(role).write(content)
    result_id = msg.get("result_id", "")
    if result_id and result_id in st.session_state.get("query_results", {}):
        raw = st.session_state["query_results"][result_id]
        clean = [{k: ("" if v is None else v) for k, v in row.items()} for row in raw]
        st.dataframe(clean, use_container_width=True)
        render_chart(raw, result_id, title=content)

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
        prompt = st.chat_input("输入分析需求，如：查询合同金额最高的前10个合同")

    if prompt:
        if not needs_process:
            st.chat_message("user").write(prompt)
            st.session_state["message"].append({"role": "user", "content": prompt})

        full_query = prompt
        schema_text = st.session_state.get("excel_schema_text", "")
        if schema_text:
            full_query = f"[上传的数据表结构]\n{schema_text}\n\n[用户问题]\n{prompt}"

        response_chunks = []
        with st.spinner("思考中..."):
            res_stream = st.session_state["agent"].execute_stream(full_query)
            for chunk in res_stream:
                response_chunks.append(chunk)
        full_response = "".join(response_chunks)

        clean_text, clarify_data = extract_clarify(full_response)

        if clarify_data:
            clean_text = strip_schema_content(clean_text) if clean_text else ""
            if clean_text:
                st.chat_message("assistant").markdown(
                    f"<small>{clean_text}</small>", unsafe_allow_html=True
                )
                st.session_state["message"].append({"role": "assistant", "content": clean_text})
            clarify_data["original_query"] = _extract_original_query(prompt)
            st.session_state["pending_clarify"] = clarify_data
            st.rerun()
        else:
            exec_result = extract_exec_result(full_response)
            if exec_result and exec_result.get("success"):
                row_count = exec_result.get("row_count", 0)
                data = exec_result.get("data", [])
                msg = f"查询完成，返回 {row_count} 行数据"
                msg_id = f"result_{len(st.session_state['message'])}"
                st.session_state["query_results"][msg_id] = data
                st.chat_message("assistant").write(msg)
                clean = [{k: ("" if v is None else v) for k, v in row.items()} for row in data]
                st.dataframe(clean, use_container_width=True)
                render_chart(data, msg_id, title=msg)
                st.session_state["message"].append({
                    "role": "assistant", "content": msg, "result_id": msg_id,
                })
            elif exec_result and exec_result.get("error"):
                st.chat_message("assistant").error(exec_result["error"])
                st.session_state["message"].append({
                    "role": "assistant", "content": f"错误：{exec_result['error']}",
                })
            else:
                content = strip_schema_content(full_response)
                # 统一兜底：如果能从回复中提取到 SQL，直接执行并展示数据
                sql_only = extract_sql_only(full_response)
                has_sql = any(kw in sql_only.upper() for kw in ("SELECT", "INSERT", "UPDATE", "DELETE"))
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
                        mid = f"result_{len(st.session_state['message'])}"
                        st.session_state["query_results"][mid] = data
                        st.chat_message("assistant").write(msg)
                        clean = [{k: ("" if v is None else v) for k, v in row.items()} for row in data]
                        st.dataframe(clean, use_container_width=True)
                        render_chart(data, mid, title=msg)
                        st.session_state["message"].append({
                            "role": "assistant", "content": msg, "result_id": mid,
                        })
                    else:
                        st.chat_message("assistant").error(f"SQL执行失败: {result.get('error', result) if result else '未知'}")
                        st.session_state["message"].append({"role": "assistant", "content": "查询失败"})
                else:
                    st.chat_message("assistant").write(content)
                    st.session_state["message"].append({"role": "assistant", "content": content})
            if needs_process:
                st.rerun()
