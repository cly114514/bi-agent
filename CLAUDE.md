# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`bi-agent` is a Streamlit-based BI (business intelligence) agent: upload an Excel file, connect to MySQL, and ask natural-language questions that get translated into SQL, executed, and rendered as tables + optional charts. All runnable code lives in the `1agent/` subdirectory.

## Commands

All commands run from the `1agent/` directory.

```bash
cd 1agent
pip install -r requirements.txt
streamlit run app.py
```

- **Web UI**: `streamlit run app.py` (single Streamlit entry point)
- **ChromaDB inspection** (verify the SQL example vector store is loaded): `python view_chromadb.py`
- **Re-index vector store** (rebuild ChromaDB from `data/sql_examples.txt`): delete `1agent/chroma_db/` and the `md5.text` cache, then run the app once — `VectorStoreService.load_document()` runs lazily and ingests any new file by MD5.
- **Smoke-test the agent loop without UI**:
  ```bash
  python -m agent.react_agent
  ```
  This streams a sample query ("查询华东地区的销售总额") and prints chunks.

There is no test suite, linter, or formatter configured in the repo. The `__pycache__/` directories and per-day `logs/agent_YYYYMMDD.log` files are runtime artifacts.

- **Import-graph regression check**: `python 1agent/selftest.py` — parses every entry point's imports with `ast`, re-executes them from `/tmp` (worst-case cwd), and exits non-zero if any project-internal `utils./agent./model./rag.` import fails. Run this after touching any of those subpackages. Streamlit's `sys.path` handling is implementation-defined; without the explicit `sys.path` bootstrap in `app.py` (and the new `__init__.py` files in `utils/`, `agent/`, `agent/tools/`, `model/`, `rag/`), `streamlit run` from a parent directory raises `ModuleNotFoundError: No module named 'utils.chart_maker'`. Both layers are required — `__init__.py` makes the packages explicit; the `sys.path` block in `app.py:1-11` puts the project root on the path.

## Required environment

Set `DEEPSEEK_API_KEY` in the environment or in a `.env` at the repo root. `app.py` also aliases it as `OPENAI_API_KEY` for LangChain compatibility. The `DASHSCOPE_API_KEY` env var is no longer accepted — set `DEEPSEEK_API_KEY` instead.

MySQL connection details (host/port/user/database) live in `1agent/config/agent.yml`; the password is requested interactively in the sidebar at runtime and stored in `st.session_state`.

## Architecture

### Request flow

1. **`1agent/app.py`** — Streamlit front-end. Manages `st.session_state` (uploaded file bytes, parsed schema, MySQL connection state, chat history, query result cache capped at 50 messages). Handles the `[CLARIFY]…[/CLARIFY]` ambiguity-resolution protocol emitted by the agent, JSON extraction from agent output, and chart-button rendering.
2. **`1agent/agent/react_agent.py`** — Thin wrapper that instantiates `LangChainToolAgent` with the four tools.
3. **`1agent/agent/langchain_agent.py`** — The actual ReAct loop. A manual message-based tool-calling loop (up to 12 turns) using `ChatOpenAI` bound to LangChain `@tool`-decorated functions. `DeepSeekChat` is a `ChatOpenAI` subclass that monkey-patches `_create_chat_result` to capture `reasoning_content` from `deepseek-v4-flash` responses and injects it back on the next request via `extra_body`.
4. **`1agent/agent/tools/agent_tools.py`** — Four tools the LLM can call:
   - `get_table_schema` — returns a textual description of the currently loaded Excel schema (column names, dtypes, sample values, dirty data flags).
   - `generate_sql` — calls `RagSummarizeService.rag_summarize` to retrieve similar SQL examples from ChromaDB and prompt the LLM to produce SQL.
   - `execute_sql` — runs SQL against MySQL. Auto-rewrites RAG-style placeholder table names (e.g. `sales`, `orders`) to the actual MySQL table name; rewrites a hard-coded English→Chinese column alias map; auto-injects `WHERE` clauses to exclude dirty values detected at parse time.
   - `execute_excel` — fallback when MySQL is not connected. Delegates to `utils.excel_query.execute_excel_query` (a rule-based NL→filter/aggregator engine, not LLM-driven).
5. **`1agent/rag/rag_service.py`** + **`1agent/rag/vector_store.py`** — ChromaDB-backed retrieval. `data/sql_examples.txt` uses a `description: SQL` line format that `_load_sql_knowledge` splits into Documents with `sql` in metadata. New files are ingested on first call; MD5 hashes are stored in `1agent/md5.text` (path from `config/chroma.yml`) to skip re-ingestion.
6. **`1agent/model/factory.py`** — Returns a `ChatOpenAI` configured for `https://api.deepseek.com/v1`. The `embed_model` factory currently returns `None` — embeddings are a stub, but the example retriever still works because `data/sql_examples.txt` descriptions are stored as document content (no embedding round-trip is actually needed for the small static example set, but Chroma is still wrapping it).

### Key design decisions and gotchas

- **Two model modes**: `model/factory.py` hard-codes `deepseek-v4-flash` with `thinking_mode: enable`. This requires the `DeepSeekChat` `reasoning_content` round-trip in `langchain_agent.py`. If you switch to `deepseek-chat`, the round-trip is a no-op (the chat model never returns `reasoning_content`), but LangChain's `bind_tools` works more reliably — the CHANGELOG recommends `deepseek-chat` for tool-calling agents.
- **`config/rag.yml` is stale**: it still says `qwen-plus` / `text-embedding-v4`, but `model/factory.py` no longer reads it for the LLM. The `rag_conf` object is only used to import `chat_model_name` in legacy paths. If you change models, edit `factory.py`, not `rag.yml`.
- **`agent/tools/middleware.py`** defines `wrap_tool_call` / `before_model` decorators but **nothing in the current agent loop applies them** — `LangChainToolAgent` calls tools directly. The file is kept for future migration to `langchain.agents`.
- **Dirty-data injection** is the core SQL-safety mechanism. `utils/excel_parser._detect_dirty_values` flags values that don't match the column's semantic type (numeric/date/bool) plus placeholder strings (`null`, `N/A`, `测试`, etc.) and zero values in numeric fields. `execute_sql` then synthesizes `WHERE col NOT IN ('脏值1', '脏值2')` / `col > 0` clauses and injects them before `ORDER BY` / `LIMIT` / `GROUP BY` / `HAVING`.
- **CLARIFY protocol**: the system prompt (`prompts/main_prompt.txt`) requires the agent to emit `[CLARIFY]{...}[/CLARIFY]` for ambiguous natural-language queries (e.g. "查询卖得最差的合同" when the table has multiple numeric columns). The Streamlit layer parses this, shows a radio button, and re-prompts the model with the user's mapping choice. Don't break the regex `CLARIFY_PATTERN` in `app.py:56` without updating both ends.
- **Schema injection into prompt**: `app.py` prepends `[上传的数据表结构]\n{schema_text}\n\n[用户问题]\n{user_prompt}` before each LLM call. The schema text is generated by `utils/excel_parser.format_schema_for_prompt` (concise header-only form, used here) and the more detailed `get_table_schema` tool output. The `strip_schema_content` function in `app.py` filters schema text out of assistant replies so it doesn't leak into the chat UI.
- **No MySQL → Excel direct query**: `execute_excel_query` in `utils/excel_query.py` is a heuristic filter/aggregator (regex-based condition parsing, Chinese keyword aggregation detection). It's a strict subset of what LLM-generated SQL can express — it handles simple `区域=华东` filters, `求和/平均/最大/最小` aggregations, and `前N` limits, but not joins, subqueries, or window functions.
- **Result JSON contract**: `execute_sql` (and the Excel fallback) must return `{"success": bool, "data": [...], "row_count": N, ...}` JSON. The Streamlit layer's `extract_exec_result` only accepts dicts containing `success` and (`data` or `error`).
- **Chart auto-detection & type switcher** (`utils/chart_maker.py`): every query result with ≥2 cols + ≥2 rows renders a Plotly chart by default. `detect_chart_options` inspects column types (date / numeric / categorical) and row count to produce a list of valid `ChartOption`s (grouped_bar, multi_line, stacked_bar, stacked_100, pie, single_bar). The default picks `grouped_bar` when ≥2 numeric cols exist, then `multi_line`, then `single_bar`. The user gets a `st.segmented_control` above the chart to flip types; selection persists in `st.session_state["chart_selection"][msg_id]` across reruns and page refreshes. Charts are re-rendered in the session_state-rerender loop (`app.py:301-306`) so refreshing the page no longer drops the chart.
- **Logs**: all modules use `utils.logger_handler.logger`, which writes to `1agent/logs/agent_YYYYMMDD.log` and the console. Tool calls, dirty-data injection, and SQL execution are all logged at INFO.
- **Chinese-aware column handling**: `_safe_col_name` in `mysql_handler.py` preserves `一-鿿` in column names (backticked). The English→Chinese alias map in `agent_tools.py` is a one-way substitution that runs before MySQL execution.

### File map (only files that aren't obvious from the directory listing)

- `1agent/app.py` — Streamlit UI, session state, JSON/CLARIFY parsing, chart rendering.
- `1agent/agent/langchain_agent.py` — ReAct loop + `DeepSeekChat` LLM subclass.
- `1agent/agent/react_agent.py` — facade: builds the agent with the four tools.
- `1agent/agent/tools/agent_tools.py` — `generate_sql` / `get_table_schema` / `execute_sql` / `execute_excel` (the heart of the system).
- `1agent/agent/tools/middleware.py` — unused LangChain middleware decorators.
- `1agent/rag/rag_service.py` — `RagSummarizeService` (prompt + retriever → SQL).
- `1agent/rag/vector_store.py` — `VectorStoreService` (Chroma wrapper + MD5-dedup ingest).
- `1agent/model/factory.py` — `ChatModelFactory` (DeepSeek), `EmbeddingsFactory` (stub).
- `1agent/model/deepseek_chat.py` — re-exports `DeepSeekChat` / `create_deepseek_llm`.
- `1agent/utils/excel_parser.py` — Excel→schema dict with dirty-value detection.
- `1agent/utils/excel_query.py` — rule-based NL→filter/agg over Excel (no-MySQL path).
- `1agent/utils/mysql_handler.py` — connection, `create_table_from_excel`, `insert_rows`, `execute_query` (returns the result dict contract).
- `1agent/utils/chart_maker.py` — Plotly chart engine. Public API: `ChartOption` (id/label/icon/requires predicate), `ShapeInfo` (column classification), `detect_chart_options(data)` (returns valid options), `build_figure(data, option)` (pure function returning a `go.Figure`), `render_chart(data, msg_id, title)` (Streamlit side: detects options, renders `segmented_control` + `st.plotly_chart`, persists selection), `get_figure_html(fig)` (for tests). Modebar exposes zoom/pan/autoscale/reset and PNG/HTML download (PNG requires `kaleido`).
- `1agent/utils/{config_handler,prompt_loader,path_tool,logger_handler,file_handler}.py` — wiring.
- `1agent/prompts/main_prompt.txt` — system prompt with the [CLARIFY] contract.
- `1agent/prompts/rag_summarize.txt` — prompt for the RAG→SQL step.
- `1agent/prompts/report_prompt.txt` — not currently invoked by the agent loop (legacy / future use).
- `1agent/data/sql_examples.txt` — line-format `描述: SQL` corpus that seeds ChromaDB.
- `1agent/view_chromadb.py` — CLI to inspect the persisted Chroma collection.
- `1agent/config/{agent,rag,chroma,prompts}.yml` — runtime configuration.

### Adding a new tool

1. Decorate the function with `@tool(description=...)` in `1agent/agent/tools/agent_tools.py`.
2. Register it in the `tools=[...]` list inside `1agent/agent/react_agent.py`'s `__init__`.
3. Update the system prompt (`1agent/prompts/main_prompt.txt`) to describe the new step in the execution flow, including any new ambiguity cases that need a `[CLARIFY]` round-trip.
4. If the tool needs session state (schema, table name), use the `set_schema` / `set_table_name` setters or add a new module-level setter — never instantiate another `MySQLHandler` inside a tool; use `utils.mysql_handler.get_mysql()`.
5. Return a JSON string (LangChain `ToolMessage` content must be a string) following the `{"success": ..., "data": ..., "row_count": ...}` contract when applicable, so `app.py`'s `extract_exec_result` can pick it up.
