# Known Issues — `feat/integrated-v2.3-chart-and-safety`

本分支以 `origin/最新的代码包` (`c5ef29c`) 为基线,集成 v2.2 的 Plotly 图表引擎 + 修复 4 项安全/工程化回归。
本文件列出**未处理**的队友代码遗留问题,留给后续 PR。

## 环境要求(集成方案未触碰,但运行 agent 必需)

- **Python 3.10+**: 基线 `c5ef29c` 的 `utils/mysql_handler.py:111` 用了 PEP 604 联合类型语法(`MySQLHandler | None`),Python 3.9 会 `TypeError: unsupported operand type(s) for |`。
- **langchain 1.0+**: `react_agent.py` 用 `from langchain.agents import create_agent`,langchain 0.3.x 没有这个符号,需 langchain 1.0.0+。
- **`DASHSCOPE_API_KEY` 环境变量**: `model/factory.py` 默认 `EmbeddingsFactory` 返回 `DashScopeEmbeddings`,未设 key 时 pydantic 校验抛 `Value error, Did not find dashscope_api_key`。

## 未修的队友 bug(用户已选"不修队友 bug")

| # | 位置 | 问题 | 触发条件 |
|---|---|---|---|
| B1 | `1agent/app.py:446-448` | `st.session_state["agent"].set_last_result_summary(...)` 在 `ReactAgent` 上未定义,触发 `AttributeError` | LLM 输出非 JSON 包装的 SQL → 走"统一兜底"路径 → 命中此调用 |
| B2 | `1agent/app.py:155-171` | `extract_sql_only` 在 `return` 后有死代码 `all_fields = set()` 等 17 行,运行不到 | 任何 `extract_sql_only(...)` 调用后分析静态时会困惑 |
| B3 | `1agent/agent/tools/agent_tools.py:8`(队友原版) | `rag = RagSummarizeService()` 模块顶层立即初始化,失败时无法降级 | 改用 v2.2 的 `_get_rag()` lazy 模式修复 |
| B4 | `1agent/model/factory.py` | `chat_model` 不再 guard 空 API key,空 key 会让 `ChatOpenAI(...)` 构造时炸 | 未设 `DEEPSEEK_API_KEY` 启动 |
| B5 | `1agent/utils/excel_parser.py` | `_detect_dirty_values` 被队友改成空 stub,所有列不再标脏值 | 上传脏数据 Excel → sqlglot 注入路径无脏值可过滤(功能降级但不崩) |
| B6 | `1agent/utils/mysql_handler.py` | 不再处理表名特殊字符(空格/括号等) | 表名含特殊字符 → MySQL 报语法错 |

## 集成方案未触动的回归(用户决定保留)

- **`langchain.agents.create_agent` 新 API**: 未回退到 v2.2 的手动 ReAct 循环。`reasoning_content` round-trip、tool-call 流式渲染需要在新 API 上重新调;当前的 `_show_data_with_chart` 只在 LLM 返回的最终 JSON 上吃结果,不依赖中间流式语义。
- **`excel_parsed_list` 多文件状态**: 未切换到 v2.2 的 `_current_files: dict[file_id, entry]` 模型。集成方案改用 v2.2 的 `agent_tools.py` 内部用 `set_files()` 兼容老 `set_schema()` 的方式做适配。
- **`prompts/main_prompt.txt` 多表 + LIKE-first dispatch**: 未重写,直接使用基线版本。

## 验证清单(用户手动跑)

环境先解决(下选一):
- 升级 Python 到 3.10+ 并 `pip install -U langchain>=1.0`
- 或在 Docker/venv 里装新环境

之后:

```bash
cd 1agent
pip install -r requirements.txt
python selftest_imports.py   # 期望:全部 PASS
streamlit run app.py
```

UI 烟测脚本见 `/Users/mac/.claude/plans/peaceful-rolling-sky.md` 验证一节。

## Commit 摘要

```
集成自 v2.2:
  - utils/chart_maker.py       Plotly 930 行(完整)
  - utils/type_inference.py    95% 阈值共享(完整)
  - selftest_imports.py        import 图自检(完整)

修复队友的 4 项回归:
  - app.py 顶部                 OPENAI_API_KEY 别名
  - app.py session_state        + chart_state / 50 条淘汰
  - app.py _show_data_with_chart  表格 + 图表统一渲染
  - agent_tools.py              execute_excel tool 回归 + sqlglot 脏值注入

依赖更新:
  - requirements.txt            +plotly>=5.18.0 +kaleido>=0.2.1 -dashscope -matplotlib -pillow -DASHSCOPE_API_KEY
  - streamlit                   >=1.28 → >=1.40 (segmented_control 需要)
```
