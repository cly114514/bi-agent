# CODE_REVIEW.md

> 本仓库代码审查报告，覆盖 2026-06-02 之前的所有代码。
> 用途：作为后续维护人员的修复路线图，每条 finding 都给出文件位置、问题描述、推荐修法。
> 配套：分支 `feat/chart-engine-plotly-v2.1`（最新已合并图表重写），主分支 `main` 仍为旧版本。

---

## 0. 如何使用本文件

| 你想做什么 | 看哪里 |
|---|---|
| 知道"现在能不能合并到 main" | §1 严重度分级表（Critical 必须先修，High 应在合并前修） |
| 找一个具体的 bug | §2 严重度清单，按文件定位 |
| 评估某条 finding 的修复成本 | 每条 finding 的 "修复成本" 字段 |
| 知道"哪些东西是好的，别瞎改" | §4 正面发现（不要破坏） |
| 跑一遍冒烟回归 | §5 自测清单 |

每条 finding 格式：
- **位置**：`path:line` 或 `path`（跨多行/多文件）
- **类别**：Security / Bug / Code Smell / Config / Test / Docs / Architecture
- **修复成本**：S(≤30min) / M(1-3h) / L(>3h)
- **状态**：🔴 未修 / 🟡 部分缓解 / ✅ 已修

---

## 1. 严重度分级表（30 条发现）

| # | 严重度 | 类别 | 简述 | 位置 | 状态 |
|---|---|---|---|---|---|
| 1 | 🔴 Critical | Security | **SQL 注入：脏值字面量直接拼到 `WHERE NOT IN (...)`** | `agent/tools/agent_tools.py:121-124` | 🔴 |
| 2 | 🔴 Critical | Bug | **`embed_model = None`：Chroma 重新导入会跑挂** | `model/factory.py:32-35` → `rag/vector_store.py:8,59` | 🔴 |
| 3 | 🔴 Critical | Architecture | **三个不同的 ChromaDB 持久化目录** | `chroma_db/` / `1agent/chroma_db/` / `1agent/agent/chroma_db/` | 🔴 |
| 4 | 🔴 Critical | Bug | **正则替换 SQL 时不避开字符串字面量** | `agent/tools/agent_tools.py:87-88, 111` | 🔴 |
| 5 | 🟠 High | Security | **模块级全局 `_current_schema/_current_table_name` 跨会话串扰** | `agent/tools/agent_tools.py:15-16, 19-26` | 🔴 |
| 6 | 🟠 High | Security | **`_safe_col_name` 静默截断到 64 字符，碰撞检测缺失** | `utils/mysql_handler.py:10-14` | 🔴 |
| 7 | 🟠 High | Bug | **`execute_sql` 用正则 `re.search` 找 `ORDER BY/LIMIT/...` 注入 WHERE，子查询/CTE/窗口函数会破** | `agent/tools/agent_tools.py:132-144` | 🔴 |
| 8 | 🟠 High | Bug | **`_find_json_in_text` 从头找 brace，会先匹配 LLM 示例里的 JSON** | `app.py:79-106` | 🔴 |
| 9 | 🟠 High | Bug | **`deepseek-v4-flash` 不是 DeepSeek 公开模型名** | `model/factory.py:25`, `agent/langchain_agent.py:100` | 🔴 |
| 10 | 🟠 High | Resource | **MySQL 连接出错时不关，泄露 fd + 撑爆 max_connections** | `utils/mysql_handler.py:49-94` | 🔴 |
| 11 | 🟠 High | Bug | **ReAct 循环硬编码 12 轮上限，超限丢弃所有之前累积的进度** | `agent/langchain_agent.py:149, 182-183` | 🔴 |
| 12 | 🟠 High | Bug | **`_reasoning_content` 实例属性跨请求串扰（race）** | `agent/langchain_agent.py:55, 64, 73, 92` | 🔴 |
| 13 | 🟠 High | Resource | **`chart_state` 永不淘汰，长期会话内存增长无界** | `app.py:166-178` vs `utils/chart_maker.py:776-777` | 🔴 |
| 14 | 🟡 Medium | Security | **`execute_sql` 自动从 YAML 恢复会读 `password: "xxxxxxxx"` 走一遍 auth** | `agent/tools/agent_tools.py:147-155` | 🔴 |
| 15 | 🟡 Medium | Code Smell | **`_create_chat_result` 模块级 monkey-patch 影响所有 ChatOpenAI 子类** | `agent/langchain_agent.py:18-38` | 🔴 |
| 16 | 🟡 Medium | Code Smell | **`agent/tools/middleware.py` 是死代码（定义了装饰器但没人用）** | `agent/tools/middleware.py` 全文件 | 🔴 |
| 17 | 🟡 Medium | Config | **`config/rag.yml` 是死配置（factory.py 硬编码，不读 YAML）** | `config/rag.yml` + `utils/config_handler.py:29` | 🔴 |
| 18 | 🟡 Medium | Config | **`config/agent.yml` 的 `external_data_path: data/external/records.csv` 指向不存在的文件** | `config/agent.yml:1` | 🔴 |
| 19 | 🟡 Medium | Config | **`factory.py` 模型名硬编码，没法用户切换** | `model/factory.py:14-29` | 🔴 |
| 20 | 🟡 Medium | Test | **`selftest.py` 名过其实（只查 import 能不能解析，名字让人误以为是测试套件）** | `selftest.py` | 🔴 |
| 21 | 🟡 Medium | Bug | **Excel 解析器 `_infer_dtype` 用 70% 阈值，图表引擎 `_is_numeric` 用 95%，同一列在两边可能分类不同** | `utils/excel_parser.py:102` vs `utils/chart_maker.py:157` | 🔴 |
| 22 | 🟡 Medium | Bug | **`extract_sql_only` 用 `rfind` 找 SQL 关键词，会匹配到散文里出现的 "SELECT"** | `app.py:192-197` | 🔴 |
| 23 | 🟡 Medium | Bug | **`has_sql` 关键词子串检查，匹配 `SELECTED/UPDATED/DELETED` 等假阳性** | `app.py:442` | 🔴 |
| 24 | 🟡 Medium | Resource | **MySQL 无连接池，每查询开/关连接（~10-50ms 开销 × N）** | `utils/mysql_handler.py:37, 96-112` | 🔴 |
| 25 | 🟡 Medium | Code Smell | **`prompts/report_prompt.txt` + `load_report_prompts` 是死代码** | `prompts/report_prompt.txt` | 🔴 |
| 26 | 🟡 Medium | Docs | **`data/sql_examples.txt` 48 行人工 SQL，摄入用 regex `split(":", 1)` 解析，SQL 里含冒号就坏** | `data/sql_examples.txt`, `rag/vector_store.py:29-52` | 🔴 |
| 27 | 🟡 Medium | Bug | **`chart_maker` 聚合时取非数值列的 `first`，一个桶里有多个产品名只显示第一个** | `utils/chart_maker.py:343` | 🔴 |
| 28 | 🟡 Medium | Bug | **`chart_maker` 聚合时静默丢掉脏 cell（pd.to_numeric coerce）但 UI 没提示** | `utils/chart_maker.py:339` | 🔴 |
| 29 | 🟡 Medium | Config | **`utils/excel_parser.py` 把所有数值列都建为 `DOUBLE`，金额类应该用 `DECIMAL(20,4)`** | `utils/mysql_handler.py:67` | 🔴 |
| 30 | 🟡 Medium | Security | **`mysql_handler` 抛出的 raw pymysql 异常直接 `st.error` 给用户看（信息泄露）** | `app.py:227, 434` | 🔴 |

> 严重度 = 修不掉/不修的影响程度 × 修复成本的反比。
> 🔴 Critical = 必须修；🟠 High = 上线前应该修；🟡 Medium = 知道就行，按 schedule 修。

---

## 2. 详细发现清单

### 🔴 Critical

#### #1 SQL 注入：脏值字面量直接拼到 `WHERE NOT IN (...)`
- **位置**：`1agent/agent/tools/agent_tools.py:121-124`
- **类别**：Security
- **修复成本**：S
- **问题**：`_detect_dirty_values` 把 Excel cell 里所有"非数字"的值都标为脏数据。然后在 `execute_sql` 里：
  ```python
  vals = ", ".join(f"'{d}'" for d in str_dirty[:10])
  extra_conditions.append(f"{col_ref} NOT IN ({vals})")
  ```
  攻击者可以上传一个 Excel，把某数值列的某个 cell 填成 `'); DROP TABLE biagent.x; --`。
  这个 cell 被识别为脏值，然后被原样拼进 SQL 字面量。如果 MySQL 启用了多语句执行（默认关闭，但低权限用户可能能开），就执行了 DDL。
  即使没有多语句，`' OR 1=1 --` 这类能彻底绕过过滤。
- **修法**：
  ```python
  # 改用参数化占位符
  placeholders = ", ".join(["%s"] * len(str_dirty[:10]))
  extra_conditions.append(f"{col_ref} NOT IN ({placeholders})")
  # 然后用 cur.execute(sql_with_placeholders, str_dirty[:10])
  ```
  或者至少用 `pymysql.converters.escape_string` 把单引号 doubling 掉。
- **测试**：加单测：上传一个 cell 含 `' OR 1=1 --` 的 Excel，跑任意查询，断言不返回全集。

#### #2 `embed_model = None`：ChromaDB 实际跑不起来
- **位置**：`1agent/model/factory.py:32-35` → `1agent/rag/vector_store.py:8, 59`
- **类别**：Bug
- **修复成本**：M
- **问题**：`EmbeddingsFactory().generator()` 返回 `None`，这个 `None` 被传给 `Chroma(persist_directory=..., embedding_function=None)`。CLAUDE.md 注释说"retriever 还能用因为 `data/sql_examples.txt` 的描述直接当 document content 存了"——这个解释是错的。Chroma 内部每次 `add_documents` 和 `query` 都要调 `embedding_function` 把文本转向量，传 `None` 会在第一次写入时直接 raise。
  之所以之前看起来"能跑"，是因为老的 chroma.sqlite3 是过去某个有 embeddings 的会话里写好的，重新加载时不重建向量。但只要换一个 collection name（`config/chroma.yml` 改 `collection_name`）或者删 chroma_db 重新 ingest，立即死。
- **修法**：
  - 方案 A：换成真实的 embedding 模型（DeepSeek 没公开 embedding，临时可用 `langchain_community.embeddings.HuggingFaceEmbeddings` + 一个开源中文模型如 `shibing624/text2vec-base-chinese`）。
  - 方案 B：把 RAG 整个去掉，改成"在 system prompt 里直接列出全部 SQL 示例"（48 条不会让 prompt 爆炸）。
  - 方案 C：保留 embedding 接口但加 `if embed_model is None: raise RuntimeError(...)` 启动时 fail-fast，不要静默接受。
- **测试**：删 chroma_db，删除 `md5.text`，跑一次 `vector_store.load_document()` 验证不挂。

#### #3 三个不同的 ChromaDB 持久化目录
- **位置**：
  - `chroma_db/` (repo 根，188KB)
  - `1agent/chroma_db/` (912KB, `vector_store.py` 真正写的位置)
  - `1agent/agent/chroma_db/` (167KB, 死目录，2025-05-30 后没动过)
- **类别**：Architecture
- **修复成本**：S
- **问题**：`chroma_conf["persist_directory"]` 在 config 里是 `chroma_db`（相对路径），通过 `get_abs_path` 解析成 `/abs/.../1agent/chroma_db`。所以**唯一**活的目录是 `1agent/chroma_db/`。其他两个：
  - repo 根的 `chroma_db/` 不知道谁建的，可能是早期脚本裸跑
  - `1agent/agent/chroma_db/` 是迁移前的遗物
  未来 maintainer 看 `git log` 会困惑"哪个才是真的"，还会不小心 commit 错的那个。
- **修法**：
  1. 删 `chroma_db/` (repo 根) 和 `1agent/agent/chroma_db/`
  2. 确认 `1agent/chroma_db/` 在 `.gitignore`（已经在了）
  3. 在 `CLAUDE.md` 写一句：`ChromaDB 唯一持久化目录: 1agent/chroma_db/`

#### #4 正则替换 SQL 时不避开字符串字面量
- **位置**：`1agent/agent/tools/agent_tools.py:87-88, 111`
- **类别**：Bug
- **修复成本**：M
- **问题**：
  ```python
  pattern = r'\b(?:' + '|'.join(example_tables) + r')\b'
  sql = _re.sub(pattern, actual_table, sql, flags=_re.IGNORECASE)
  # 然后:
  sql = re.sub(r'\b' + eng + r'\b', f"\`{cn}\`", sql, flags=re.IGNORECASE)
  ```
  这两个替换都跑在**整段 SQL 字符串**上，**不会**避开字符串字面量或注释。
  LLM 生成 `SELECT 'amount is 0' AS note FROM sales WHERE ...` → `amount` 被替换成 `` `总金额` ``，SQL 直接坏。
  列名是 `WHERE region = 'sales'` 也会被替换。
- **修法**：
  - 方案 A：用 `sqlglot.parse(sql)` 走 AST 替换 Identifier；改完再 `sqlglot.transpile(sql, read='mysql', write='mysql')[0]`。
  - 方案 B：写个简单的 tokenizer，按 `'...'` / `"..."` / `/* ... */` / `-- ...` 切分，只对 code 段做替换。
  - 方案 C（应急）：在 system prompt 里强约束 LLM 不要在 SQL 里写字符串字面量包含列名。
- **测试**：加单测，SQL `SELECT 'amount' AS note FROM sales` 经过替换后必须仍可解析。

---

### 🟠 High

#### #5 模块级全局 `_current_schema/_current_table_name` 跨会话串扰
- **位置**：`1agent/agent/tools/agent_tools.py:15-16, 19-26`
- **类别**：Security / Bug
- **修复成本**：M
- **问题**：
  ```python
  _current_schema: dict = {}
  _current_table_name: str = ""
  
  def set_schema(schema): global _current_schema; _current_schema = schema
  ```
  这些是**进程级全局变量**。Streamlit 单进程多 tab/多用户部署下，A 用户上传文件后，B 用户的查询会用 A 的 schema。当用户在 agent 还在跑的时候切文件（罕见但可能），也会读到半新半旧的状态。
  线程安全也没保证——`set_schema` / `set_table_name` 在 Streamlit 主线程调，`get_table_schema` / `execute_sql` 在 LangChain 的 tool invocation 线程读。
- **修法**：
  - 方案 A：把状态搬到 `st.session_state`，tool 通过 `RunnableConfig` 拿 session。
  - 方案 B：tool 改成 factory：`make_tools(schema) -> list[Tool]`，agent 启动时绑定 schema。
  - 方案 C（最小改）：加 `threading.Lock`，并在 docstring 写明"仅单进程单用户使用"。

#### #6 `_safe_col_name` 静默截断到 64 字符
- **位置**：`1agent/utils/mysql_handler.py:10-14`
- **类别**：Security / Bug
- **修复成本**：S
- **问题**：
  ```python
  def _safe_col_name(name: str) -> str:
      name = re.sub(r"[^\w一-鿿]", "_", str(name))
      name = re.sub(r"_+", "_", name).strip("_")
      return f"`{name[:64]}`"
  ```
  两个列名 `客户ID_华东大区2025Q3详细销售额` (18 字) 和 `客户ID_华东大区2025Q3详细回款额` (18 字) — 前 64 字符相同 → MySQL `Duplicate column name` 报错。
- **修法**：
  ```python
  def _safe_col_name(name: str, used: set[str]) -> str:
      safe = re.sub(r"[^\w一-鿿]", "_", str(name)).strip("_")
      if len(safe) > 64:
          digest = hashlib.md5(safe.encode()).hexdigest()[:8]
          safe = f"{safe[:55]}_{digest}"
      while safe in used:
          # 加 collision suffix
          ...
      used.add(safe)
      return f"`{safe}`"
  ```
  caller 传一个 `used: set[str]` 进去，重复名加 `_2`, `_3` 后缀。

#### #7 `execute_sql` 用正则找 `ORDER BY/LIMIT/...` 注入 WHERE
- **位置**：`1agent/agent/tools/agent_tools.py:132-144`
- **类别**：Bug
- **修复成本**：M（和 #4 一起用 sqlglot 解决）
- **问题**：
  ```python
  injection_point = len(sql)
  for kw in ["ORDER BY", "LIMIT", "GROUP BY", "HAVING"]:
      m = re.search(r'\b' + kw + r'\b', sql, re.IGNORECASE)
      if m:
          injection_point = min(injection_point, m.start())
  ```
  对下面这些 SQL 全部会乱：
  - 子查询：`SELECT * FROM (SELECT * FROM t ORDER BY id) sub LIMIT 5` → 把 `WHERE` 塞在第一个 `ORDER BY` 前，外层查询全乱。
  - 窗口函数：`SELECT *, ROW_NUMBER() OVER (ORDER BY id) rn FROM t LIMIT 10` → 同样乱。
  - CTE：`WITH cte AS (SELECT * FROM t ORDER BY x) SELECT * FROM cte LIMIT 5` → 乱。
- **修法**：用 `sqlglot.parse_one(sql, read='mysql')`，定位到**最外层**的 `WHERE` 节点，注入。如果解析失败（语法错的 SQL），就跳过注入并 `logger.warning`，让原始 SQL 走执行。
- **测试**：把上面三个 SQL 跑一遍，断言生成的 SQL 仍能执行且 `WHERE` 注入到了正确位置。

#### #8 `_find_json_in_text` 从头找 brace，匹配示例里的 JSON
- **位置**：`1agent/app.py:79-106`
- **类别**：Bug
- **修复成本**：S
- **问题**：函数从**第一个** `{` 开始配对。LLM 输出里如果先写了举例（"比如 `{"success": true, "data": [...]}`"），会被优先匹配。即使 example 不完全符合 `extract_exec_result` 的要求，也可能在多次解析中撞中假阳性。
- **修法**：
  ```python
  # 优先找 known anchor; 找不到再 brace-match
  m = re.search(r'\{"success"\s*:', text)
  if m:
      # 从 m.start() 开始 brace-match
  else:
      # 退而求其次: brace-match 最后一个
      start = text.rfind('{')
      ...
  ```
- **测试**：构造 input `"示例: {\"success\": false, \"error\": \"fake\"} 实际结果: {\"success\": true, ...}"`，断言解析到的是实际结果那个。

#### #9 `deepseek-v4-flash` 不是 DeepSeek 公开模型名
- **位置**：`1agent/model/factory.py:25`, `1agent/agent/langchain_agent.py:100`
- **类别**：Bug / Docs
- **修复成本**：S（如果换模型）或 M（如果保留）
- **问题**：DeepSeek 公开模型是 `deepseek-chat`（对应 V3）和 `deepseek-reasoner`（R1）。`deepseek-v4-flash` 在官方 API 文档里查不到。整个 `reasoning_content` round-trip（langchain_agent.py:18-95）是基于私有 / 内网 API 写的。新人按 README 配 `DEEPSEEK_API_KEY` 走 `https://api.deepseek.com/v1` 会得到 404。
- **修法**：
  - 决定走哪个 API：官方 `deepseek-chat`（无 reasoning_content，但工具调用稳定）或私有 `deepseek-v4-flash`。
  - 把模型名和 base_url 都从 `agent.yml` 读：
    ```yaml
    llm:
      model: deepseek-chat
      base_url: https://api.deepseek.com/v1
      thinking_mode: enable
    ```
  - 在 CLAUDE.md 写明：用户用的什么模型、什么 endpoint、是否需要 reasoning_content。
  - 加启动时 sanity check：发一个 "ping" 消息，404 就立刻 fail-fast。

#### #10 MySQL 连接出错时不关
- **位置**：`1agent/utils/mysql_handler.py:49-94` (`create_table_from_excel`, `insert_rows`)
- **类别**：Resource
- **修复成本**：S
- **问题**：
  ```python
  conn = self._connect()
  cur = conn.cursor()
  cur.execute(...)  # 如果这里抛
  conn.commit()      # 不到
  conn.close()       # 不到
  ```
  连接泄漏。Streamlit 长会话里反复点"导入 MySQL"会把 server 的 `max_connections` 撑满。
- **修法**：
  ```python
  def create_table_from_excel(self, ...):
      conn = self._connect()
      try:
          cur = conn.cursor()
          cur.execute(...)
          ...
          conn.commit()
      finally:
          conn.close()
  ```
  或者更彻底：用 `contextlib.closing(self._connect())` 或 `with` 协议。

#### #11 ReAct 循环硬编码 12 轮上限
- **位置**：`1agent/agent/langchain_agent.py:149, 182-183`
- **类别**：Bug / UX
- **修复成本**：S
- **问题**：
  ```python
  for turn in range(12):
      ...
  if turn >= 11:
      yield "会话过长，请重新开始"
  ```
  12 是个魔法数。LLM 实际场景：get_table_schema → generate_sql → execute_sql → 失败重试 → 再 execute_sql → 拿到结果，已经 5 轮。复杂点的（含 [CLARIFY]）会更长。**更糟糕的是**：`yield "会话过长"` 是循环**之后**才 yield 的，循环里 yield 的所有 partial progress 已经在 stream buffer 里了——下游拿到时这个 warning 覆盖了所有之前的内容，partial result 全丢。
- **修法**：
  1. 改成 `max_turns: int = 12` 构造参数
  2. 改成 `for turn in count():` 配合 `if turn > self.max_turns: yield warning; break`
  3. 或用 `try: ... for _ in range(max_turns): ... except TurnLimitExceeded: yield warning`

#### #12 `_reasoning_content` 实例属性跨请求串扰
- **位置**：`1agent/agent/langchain_agent.py:55, 64, 73, 92`
- **类别**：Bug / Concurrency
- **修复成本**：M
- **问题**：`self._reasoning_content` 在 `DeepSeekChat` 实例上。Agent 是单例（`st.session_state["agent"] = ReactAgent()`，整进程一个）。如果两个 streamlit 会话同时用同一个 agent 实例，A 用户的 `reasoning_content` 会被注入到 B 用户的下一次请求体里。
  即便单用户、单一请求，async / 多 turn 切换之间也有竞态。
- **修法**：
  1. `self._reasoning_content` 删掉
  2. 只通过 `AIMessage.additional_kwargs["reasoning_content"]` 在 message thread 里传递
  3. 在 `_get_request_payload` 里把 thread 最后一条 `AIMessage.additional_kwargs["reasoning_content"]` 注入到下一次 `extra_body`

#### #13 `chart_state` 永不淘汰
- **位置**：`1agent/app.py:166-178` vs `1agent/utils/chart_maker.py:776-777`
- **类别**：Resource
- **修复成本**：S
- **问题**：
  ```python
  # app.py
  chart_sel = st.session_state.get("chart_selection")
  if isinstance(chart_sel, dict):
      chart_sel.pop(oldest, None)  # 删的是 chart_selection
  
  # chart_maker.py
  chart_state = st.session_state.setdefault("chart_state", {})
  state = chart_state.get(msg_id, {})  # 写的是 chart_state
  ```
  **两个不同的 key**。`_cache_result` 淘汰时只清 `chart_selection`，从不碰 `chart_state`。所以一个长会话里 `chart_state` 会无限增长（每条查询加一个 entry，每条 entry 又有 3-4 个 segmented_control 选择）。
- **修法**：
  - 统一所有 per-msg state 到一个 key：`st.session_state["results"][msg_id] = {"data": [...], "type": "multi_line", "agg": "auto", ...}`
  - 单点淘汰
  - 或者最小修：把 `chart_sel.pop(oldest, None)` 改成 `st.session_state.get("chart_state", {}).pop(oldest, None)`

---

### 🟡 Medium（按文件 / 主题分组）

#### A. `agent_tools.py` 的剩余小问题

- **#14 自动从 YAML 恢复会撞 placeholder password**
  - 位置：`1agent/agent/tools/agent_tools.py:147-155`
  - 类别：Security
  - 成本：S
  - 修法：检测 `cfg.get("password")` 是空 / 是 `"xxxxxxxx"` / 是 `your_password_here` 时直接 raise "请在侧边栏输入 MySQL 密码"，**不要**静默尝试连接。

- **#21 数值列识别门槛：parser 70% vs chart 95% 不一致**
  - 位置：`utils/excel_parser.py:102` vs `utils/chart_maker.py:157`
  - 类别：Bug
  - 成本：S
  - 修法：把 `_is_numeric` / `_infer_dtype` 抽到 `utils/type_inference.py`，统一阈值 95%。

- **#22 `extract_sql_only` 用 `rfind` 找 SQL 关键词**
  - 位置：`1agent/app.py:192-197`
  - 类别：Bug
  - 成本：S
  - 修法：先用 markdown 代码块提取（`` ```sql ... ``` `` 或 ` ``` `），没匹配上再退到 `rfind`。

- **#23 `has_sql` 关键词子串检查假阳性**
  - 位置：`1agent/app.py:442`
  - 类别：Bug
  - 成本：S
  - 修法：`re.search(r'\b(SELECT|INSERT|UPDATE|DELETE)\b', sql_only, re.IGNORECASE)`。

#### B. `langchain_agent.py` 的剩余小问题

- **#15 `_create_chat_result` 模块级 monkey-patch**
  - 位置：`1agent/agent/langchain_agent.py:18-38`
  - 类别：Code Smell
  - 成本：S
  - 修法：删掉 lines 18-38（模块级 patch），`DeepSeekChat._create_chat_result` 实例方法（line 76-95）已经做了同样的事。

- **#16 `agent/tools/middleware.py` 死代码**
  - 位置：`1agent/agent/tools/middleware.py` 全文件
  - 类别：Code Smell
  - 成本：S
  - 修法：删除整个文件，或加 `# DEPRECATED: 等待 langchain.agents 迁移` 并移动到 `_legacy/`。

#### C. 配置 & 文档

- **#17 `config/rag.yml` 死配置**
  - 位置：`config/rag.yml`, `utils/config_handler.py:29`
  - 类别：Config
  - 成本：S
  - 修法：删除 `rag.yml` 和 `rag_conf` 变量。或者把模型选择改成读它（见 #19）。

- **#18 `config/agent.yml` 的 `external_data_path: data/external/records.csv` 指向不存在的文件**
  - 位置：`config/agent.yml:1`
  - 类别：Config
  - 成本：S
  - 修法：删掉这行（grep 确认没人读它：`grep -rn external_data_path 1agent --include="*.py"` 当前已确认无结果）。

- **#19 `factory.py` 模型名硬编码**
  - 位置：`1agent/model/factory.py:14-29`
  - 类别：Config
  - 成本：M
  - 修法：读 `agent.yml` 的 `llm.model` / `llm.base_url`，env vars 覆盖：
    ```python
    cfg = agent_conf.get("llm", {})
    model = os.environ.get("BI_AGENT_LLM_MODEL") or cfg.get("model", "deepseek-chat")
    base_url = os.environ.get("BI_AGENT_LLM_BASE_URL") or cfg.get("base_url", "https://api.deepseek.com/v1")
    ```

- **#20 `selftest.py` 名字误导**
  - 位置：`1agent/selftest.py`
  - 类别：Test
  - 成本：S
  - 修法：重命名 `selftest_imports.py`，加 `tests/` 目录 + 真正的 pytest 套件覆盖 #1, #4, #5, #7, #8, #11。

- **#25 `prompts/report_prompt.txt` + `load_report_prompts` 死代码**
  - 位置：`1agent/prompts/report_prompt.txt`, `1agent/utils/prompt_loader.py:34-46`
  - 类别：Code Smell
  - 成本：S
  - 修法：删。或者真的在侧边栏加个"生成报告"按钮（覆盖 future 工作）。

- **#26 `data/sql_examples.txt` 摄入用 `split(":", 1)` 解析**
  - 位置：`1agent/rag/vector_store.py:29-52`
  - 类别：Docs
  - 成本：M
  - 修法：改成 JSONL 格式（`{"description": "...", "sql": "..."}`），加 `jsonschema` 验证，启动时校验。

#### D. `chart_maker.py` 的剩余小问题

- **#27 聚合时取非数值列的 `first`，多产品名只显示第一个**
  - 位置：`1agent/utils/chart_maker.py:343`
  - 类别：Bug
  - 成本：S
  - 修法：把 `non_numeric` 列的聚合从 `first` 改成 `lambda x: ", ".join(sorted(set(str(v) for v in x if pd.notna(v))))`，并加到图表的 hover tooltip。

- **#28 聚合时静默丢脏 cell 但 UI 不提示**
  - 位置：`1agent/utils/chart_maker.py:339`
  - 类别：Bug
  - 成本：S
  - 修法：聚合前计算 `(原始 - 聚合后*0) / 原始` 比例，在 `meta` 里加 `"excluded_pct"`，render_chart 显示 "已排除 N% 异常值"。

#### E. `mysql_handler.py` 的剩余小问题

- **#24 MySQL 无连接池，每查询开/关连接**
  - 位置：`1agent/utils/mysql_handler.py:37, 96-112`
  - 类别：Resource
  - 成本：M
  - 修法：用 `dbutils.pooled_db.PooledDB`，mincached=1, maxcached=5, blocking=True。

- **#29 数值列都建为 `DOUBLE`，金额应用 `DECIMAL`**
  - 位置：`1agent/utils/mysql_handler.py:67`
  - 类别：Bug
  - 成本：S
  - 修法：判断列名含 `excel_parser._NUMERIC_FIELD_KEYWORDS` 里的（金额/价格/数量/收入/成本/利润/单价/总额/费用/薪资）时用 `DECIMAL(20, 4)`，否则 `DOUBLE`。

#### F. UI / 信息泄露

- **#30 把 raw pymysql 异常直接 `st.error` 给用户看**
  - 位置：`1agent/app.py:227, 434`
  - 类别：Security
  - 成本：S
  - 修法：把 pymysql 错误映射成中文友好消息（"连接被拒绝 / 认证失败 / 表不存在 / SQL 语法错"），原始错误进 logger。

---

## 3. 推荐修复顺序

| 阶段 | 范围 | 估时 | 阻塞什么 |
|---|---|---|---|
| **Phase 1: Security P0** | #1, #4, #14 | 半天 | 必须先修，这是真漏洞 |
| **Phase 2: Correctness P0** | #2, #3, #6, #8, #12, #13 | 1 天 | 影响功能正确性 + 内存泄漏 |
| **Phase 3: SQL 重构** | #5, #7, #21 | 1-2 天 | sqlglot 替换 regex 是一次性大改 |
| **Phase 4: 配置 & UX** | #9, #10, #11, #15, #16, #17, #18, #19, #22, #23, #27, #28, #29, #30 | 1-2 天 | 清理 + 模型可切换 + 错误信息 |
| **Phase 5: 测试基建** | #20 + 把所有 `__main__` 块转为 pytest | 1 天 | 长期防回归 |
| **Phase 6: 死代码清理** | #25, #26 | 半天 | 降低 future maintainer 困惑 |

总计约 **5-6 天**，分散到 2-3 个 PR。

---

## 4. 正面发现（不要破坏这些）

- **`.gitignore` 写得很全**：`.env`、`__pycache__/`、`logs/`、`picture/`、`.DS_Store`、`.claude/settings.local.json` 都在列表里。新加 secrets / artifacts 时记得追加。
- **`app.py` MySQL 密码输入**用 `type="password"` 仅存 session_state，不进 URL / 日志。
- **`chart_maker.py` 内部组织清晰**：60 字符分隔条把"列推断 / 时间聚合 / 量级检测 / 双 Y / 预处理 / 图表构建 / Streamlit 入口"明确分区。每个 builder 同形签名，公共样式 `_apply_common_layout` 收口。
- **新加的 `__init__.py` + `selftest.py` 机制**虽然现在简陋（见 #20），但**架构上是对的**：把 namespace package 升格 + 加 import 图验证，是能挡住一类大错的正确方向。
- **`execute_sql` 的脏数据自动过滤**对正常数据非常有用，去掉会丢失重要的产品功能。
- **`_find_json_in_text` 的 brace-balanced parser**主体逻辑写得好（处理嵌套 + 字符串转义），只是需要锚定 `{"success":` 优先匹配（见 #8）。
- **`.env` 文件 + `load_dotenv` 显式路径**是正确做法，密钥不进仓库。
- **每个入口文件 `if __name__ == "__main__":` 的烟测数据**（5 行 excel_query 样本、668 行 chart_maker 样本）值得保留，转成 pytest fixture。

---

## 5. 自测清单

每次改完上面任何一条，跑一遍：

```bash
cd 1agent
python3 selftest.py                              # import 图健全
streamlit run app.py                             # 启动不挂
# UI 流程:
# 1. 上传一个简单 Excel
# 2. 不连 MySQL, 直接问 "看看销售额"
# 3. 应自动回退到 Excel 直查, 出 dataframe + Plotly 图表
# 4. 切换 segmented_control 6 种图都正常
# 5. 切到"原始"模式, 看 668 个原始点
# 6. 关闭浏览器, 重开 localhost:8501, 图表仍在
```

SQL 修复（#1, #4, #7, #8）后额外测：

```python
# agent_tools.py 的 SQL 注入 + corruption 测试
from agent.tools.agent_tools import execute_sql
import json

# case 1: 脏值含单引号
r = json.loads(execute_sql.func("SELECT * FROM t WHERE col > 0"))
# 上传一个 cell 含 "') OR 1=1 --" 的 Excel, 再跑上面这行
# 期望: result["success"] == False, 不是全集

# case 2: SQL 字符串里含 'amount'
r = json.loads(execute_sql.func("SELECT 'amount is 0' AS note FROM t"))
# 期望: 成功, note 列是 'amount is 0', 不被替换成 `总金额`
```

并发（#5, #12）后额外测：

```python
# 模拟两个 session 并发调 execute_sql
import threading
def worker1(): execute_sql.func("SELECT 1")
def worker2(): execute_sql.func("SELECT 2")
# 同时跑, 验证 A 的 schema/table 不会污染 B
```

---

## 6. 元信息

- **审查人**: Claude (MiniMax-M3) 在用户授权下
- **审查日期**: 2026-06-02
- **审查范围**: 所有 `.py` 文件、`.yml` 配置、`.md` 文档、prompt 模板
- **审查深度**: 完整阅读全部 10+ 模块, 加上对最近 5 次 commit 的上下文理解
- **未审查**:
  - `1agent/chroma_db/` 二进制内容（用工具看 hex 不在审查范围）
  - `1agent/agent/chroma_db/` 同上
  - `1agent/data/test_data.sql` (71KB) 只看了开头几行确认是 SQL dump
  - `1agent/data/external/sql样例` 完整内容（无扩展名, 中文文件名, 不可执行）
  - 浏览器端实际渲染表现（无 E2E 截图能力, 只能看图说话）

> 本报告的 30 条 finding 中, **🔴 3 条是真安全漏洞** (#1, #4 是 SQL 相关; #5 的会话串扰是数据污染), **🟠 7 条是会让真实用户碰到的 bug**, 其余是代码质量和工程化问题。
