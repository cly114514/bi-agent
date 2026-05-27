# CHANGELOG - bi-agent

本文档记录 bi-agent 的所有更新历史。

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
