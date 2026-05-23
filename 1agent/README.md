# BI Agent 看板智能体

基于 ReAct Agent + RAG 的智能数据查询系统，上传 Excel 后能用自然语言生成 SQL 并查询 MySQL，返回结构化数据。

## 主要能力

- **Excel 上传解析**：自动提取表头字段、数据类型、唯一值、脏数据标记
- **自然语言 → SQL**：RAG 向量检索 SQL 示例 + LLM 生成精准 SQL
- **歧义消除**：字段名无法精确匹配时弹出选项让用户选择
- **数据清洗**：自动识别并排除脏数据（类型不匹配、乱码、零值等）
- **MySQL 集成**：解析后一键导入 MySQL，SQL 直接查询真实数据库
- **SQL 审查**：生成 SQL 后自动审查表名、列名、排序方向、语法
- **Streamlit 界面**：侧边栏上传 + 对话式交互

## 使用方法

### 1. 安装依赖

```bash
pip install streamlit langchain langchain-chroma langchain-community dashscope pymysql openpyxl python-dotenv pyyaml
```

### 2. 配置环境变量

设置阿里云百炼 API Key：

```bash
set DASHSCOPE_API_KEY=你的百炼API密钥
```

或在项目根目录创建 `.env` 文件：

```
DASHSCOPE_API_KEY=你的百炼API密钥
```

### 3. 配置 MySQL

编辑 `config/agent.yml`，修改数据库连接信息：

```yaml
mysql:
  host: 127.0.0.1
  port: 3306
  user: root
  password: "你的MySQL密码"
  database: biagent
```

### 4. 启动

```bash
streamlit run app.py
```

### 5. 使用流程

1. 侧边栏 **上传 Excel** → 自动显示字段列表
2. 输入 MySQL **密码点击连接**
3. 点击 **📥 导入 MySQL 数据库**
4. 在对话框输入分析需求，如：
   - `查询前十个合同`
   - `查询总金额最高的三个合同`
   - `查询卖得最差的合同`（触发歧义消除）
5. 查看返回的数据表格

### 6. 切换模型

编辑 `config/rag.yml`：

```yaml
chat_model_name: qwen-plus        # 可选 qwen3-max / qwen-turbo 等
embedding_model_name: text-embedding-v4
```

## 项目结构

```
├── app.py                  # Streamlit 主界面
├── config/
│   ├── agent.yml           # MySQL + 外部数据配置
│   └── rag.yml             # 模型名称配置
├── agent/
│   ├── react_agent.py      # ReAct Agent 主控
│   └── tools/
│       ├── agent_tools.py  # generate_sql / get_table_schema / execute_sql
│       └── middleware.py   # 工具调用监控
├── model/
│   └── factory.py          # LLM 模型工厂
├── rag/
│   ├── rag_service.py      # RAG SQL 生成服务
│   └── vector_store.py     # ChromaDB 向量存储
├── utils/
│   ├── excel_parser.py     # Excel 解析 + 脏数据检测
│   ├── mysql_handler.py    # MySQL 连接/建表/查询
│   └── ...
├── prompts/
│   └── main_prompt.txt     # Agent 系统提示词
├── data/
│   └── sql_examples.txt    # SQL 示例向量库数据源
└── chroma_db/              # ChromaDB 持久化目录
```

## 必备库

| 库 | 用途 |
|---|------|
| `streamlit` | Web 界面 |
| `langchain` + `langchain-chroma` | Agent 框架 + 向量存储 |
| `langchain-community` | ChatTongyi / Embeddings 集成 |
| `dashscope` | 阿里云百炼 SDK |
| `pymysql` | MySQL 连接 |
| `openpyxl` | Excel 读写 |
| `python-dotenv` | 环境变量加载 |
| `pyyaml` | YAML 配置解析 |
