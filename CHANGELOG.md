# CHANGELOG - bi-agent

本文档记录 bi-agent 的所有更新历史。

---

## [v2.1.0] - 2026-06-02

### 图表子系统重写：Plotly 引擎 + 自动渲染

#### 核心改动

**1. 引擎迁移：matplotlib → Plotly**

- `utils/chart_maker.py` 全部重写，基于 `plotly.graph_objects` + `kaleido`
- 弃用 `_ensure_cn_font` 硬编码字体路径，改用系统字体栈（`PingFang SC, Microsoft YaHei, Hiragino Sans GB, Noto Sans CJK SC`）
- 支持 Streamlit 主题联动：`plotly_white` / `plotly_dark` 自动切换
- 每张图自带 modebar：缩放、平移、自动缩放、下载 PNG、下载 HTML、重置

**2. 自动渲染 + 类型切换器**

- 删掉"📊 生成图表"按钮；图表与表格**同时**渲染
- 图表上方 `st.segmented_control` 列出当前数据形状下所有可用的图表类型
- 用户切换类型时图表立即重画，刷新浏览器后选择持久化
- 修复了之前刷新页面后图表消失的 bug

**3. 6 种图表类型 + 数据驱动推断**

| 类型 ID | 标签 | 适用场景 |
|---|---|---|
| `grouped_bar` | 分组柱状图 | 1 个分类 + ≥2 数值（默认） |
| `multi_line` | 多折线 | ≥2 数值（含时间序列） |
| `stacked_bar` | 堆叠柱状图 | 同上且数值非负 |
| `stacked_100` | 100% 堆叠 | 同上且 ≤12 行 |
| `pie` | 饼图 | 1 数值、2-8 行、全部非负 |
| `single_bar` | 单系列柱 | 1 数值、2-50 行 |

自动推断规则见 `utils/chart_maker.py:detect_chart_options`。

**4. 删除 AI 配图功能**

- 移除 `generate_ai_image`（原 DashScope wanx 调用）
- 移除 `app.py:_AI_CHART_KEYWORDS` 与 `chart_type == "ai"` 分支
- 移除 `requirements.txt` 中的 `dashscope` 与 `matplotlib`、`pillow` 依赖

**5. DASHSCOPE_API_KEY 兼容回退全部下线**

- `app.py:3`、`agent/langchain_agent.py`（两处）、`model/factory.py` 都不再读 `DASHSCOPE_API_KEY`
- 现在只读 `DEEPSEEK_API_KEY`（`OPENAI_API_KEY` 仍作为 LangChain 别名保留）

**6. 依赖变化**

```
+ plotly>=5.18.0       # 主图表引擎
+ kaleido>=0.2.1       # PNG 静态导出
+ streamlit>=1.40.0    # segmented_control 需要
- dashscope>=1.14.0
- matplotlib>=3.7.0
- pillow>=10.0.0
```

#### 文件变更清单

```
新增/重写:
  1agent/utils/chart_maker.py     # Plotly 完整重写, 新增 ChartOption/ShapeInfo/detect_chart_options/build_figure/render_chart/get_figure_html

修改:
  1agent/app.py                   # 删 ~110 行旧图表助手, 加 _show_data_with_chart + 修复 rerun 不重画图表的 bug
  1agent/agent/langchain_agent.py # 删 DASHSCOPE_API_KEY 兜底
  1agent/model/factory.py         # 删 DASHSCOPE_API_KEY 兜底
  1agent/requirements.txt         # 依赖更新
```

#### 升级指南

```bash
pip install -U -r 1agent/requirements.txt
# 旧用户的 .env 中如有 DASHSCOPE_API_KEY=... 可以删掉
# 现只需 DEEPSEEK_API_KEY=sk-xxxxx
```

---

## [v2.0.0] - 2026-05-27

### 重大更新：DeepSeek API 集成 + LangChain 重构

#### 核心改动

**1. 模型从阿里云百炼 (DashScope) 迁移到 DeepSeek**

- 支持 `deepseek-chat` 和 `deepseek-v4-flash` 模型
- DeepSeek API 地址：`https://api.deepseek.com/v1`
- 环境变量：`DEEPSEEK_API_KEY`（原 `DASHSCOPE_API_KEY` 仍可兼容）

```python
# 新增 langchain_agent.py — DeepSeek LLM + LangChain Agent
from agent.langchain_agent import create_agent, LangChainToolAgent

agent = create_agent(tools=[generate_sql, get_table_schema, execute_sql])
for chunk in agent.execute_stream("查询华南地区销售额"):
    print(chunk, end="", flush=True)
```

**2. LangChain Agent 架构重构**

- 新增 `agent/langchain_agent.py`：
  - `DeepSeekChat`：LangChain ChatOpenAI 子类，支持 reasoning_content 自动处理
  - `LangChainToolAgent`：手动工具调用循环，使用 LangChain `@tool` 装饰器
  - `create_agent()`：工厂函数

- 重写 `agent/react_agent.py`：
  - 从纯 OpenAI 客户端实现改为使用 `LangChainToolAgent`
  - 保持 `ReactAgent` 接口不变

**3. deepseek-chat vs deepseek-v4-flash 选择**

| 模型 | 工具调用 | reasoning_content | 推荐场景 |
|------|---------|-----------------|---------|
| `deepseek-chat` | ✅ 原生支持 | ❌ 不需要 | **默认使用** |
| `deepseek-v4-flash` | ⚠️ 需额外处理 | ✅ 必须回传 | 高级用户 |

> **注意**：`deepseek-v4-flash` 在工具调用场景下需要 `reasoning_content` 往返传递，LangChain 当前无法完全支持。推荐使用 `deepseek-chat`。

**4. reasoning_content 处理机制**

```python
class DeepSeekChat(ChatOpenAI):
    # 1. 捕获：monkey-patch _create_chat_result 提取 reasoning_content
    # 2. 存储：存入 self._reasoning_content
    # 3. 回传：_generate / _get_request_payload 自动注入 extra_body
    # 4. robust：即使 deepseek-chat 不需要，也安全处理
```

#### 图表生成功能

**5. 新增图表生成模块**

- `utils/chart_maker.py`：
  - `render_data_chart(data, chart_type)` — matplotlib 生成柱状图/饼图/折线图，返回 PNG bytes
  - `generate_ai_image(caption)` — 调用阿里云通义万相 (wanx) API 生成配图

- Streamlit 界面集成：
  - 查询结果表格下方自动显示 📊 生成图表 按钮
  - 支持关键词检测（"对比"→柱状图，"占比"→饼图，"趋势"→折线图）

```python
# 使用示例
from utils.chart_maker import render_data_chart, generate_ai_image

# matplotlib 数据图表
img_bytes = render_data_chart(data, chart_type="bar", title="各区域销售额")
st.image(img_bytes, use_container_width=True)

# AI 配图（需配置 DASHSCOPE_API_KEY）
img_url = generate_ai_image("一个数据分析师在查看销售报表")
st.image(img_url)
```

#### 其他更新

**6. 新增 `model/deepseek_chat.py`** — DeepSeek LLM 封装入口

**7. requirements.txt 更新**：
```
matplotlib>=3.7.0
pillow>=10.0.0
```

**8. Streamlit 启动脚本**：
- `start.sh`（Mac/Linux）
- `start.bat`（Windows）

---

## [v1.x.x] - 历史版本

详见早期提交记录。核心功能：
- Excel 上传解析（字段类型、唯一值、脏数据标记）
- RAG 向量检索 SQL 示例
- 自然语言生成 SQL
- 歧义消除（字段名不精确匹配时弹窗选择）
- MySQL 导入和查询
- Streamlit 对话界面

---

## 升级指南

### 从旧版本升级

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 安装/更新依赖
pip install -r requirements.txt

# 3. 设置 DeepSeek API Key
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# 4. 启动
streamlit run app.py
```

### 环境变量对比

| 旧（百炼） | 新（DeepSeek） | 说明 |
|------------|----------------|------|
| `DASHSCOPE_API_KEY` | `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `qwen-plus` 等 | `deepseek-chat` | 模型名称 |

---

## 文件变更清单

```
新增文件：
  1agent/agent/langchain_agent.py   # DeepSeek LLM + LangChain Agent 核心
  1agent/model/deepseek_chat.py     # DeepSeek LLM 导出接口
  start.sh                          # Mac/Linux 启动脚本
  start.bat                         # Windows 启动脚本

修改文件：
  1agent/agent/react_agent.py       # 改用 LangChainToolAgent
  1agent/utils/chart_maker.py        # 图表生成模块
  1agent/app.py                     # 集成图表按钮逻辑
  requirements.txt                   # 添加 matplotlib, pillow
```
