from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool
from model.factory import chat_model


@tool
def generate_sql(query: str) -> str:
    """根据用户需求生成SQL查询语句"""
    from rag.rag_service import RagSummarizeService
    rag = RagSummarizeService()
    return rag.rag_summarize(query)


@tool
def get_table_schema() -> str:
    """获取当前已上传数据表的字段结构信息"""
    from agent.tools.agent_tools import _current_schema
    from agent.tools.agent_tools import get_table_schema as _get_schema
    return _get_schema.func()


@tool
def execute_sql(sql: str) -> str:
    """在MySQL数据库中执行SQL查询语句"""
    from agent.tools.agent_tools import execute_sql as _exec
    return _exec.func(sql)


class ReactAgent:
    def __init__(self):
        self.tools = [generate_sql, get_table_schema, execute_sql]
        self.llm = chat_model
        self.system_msg = SystemMessage(content=(
            "你是 BI 看板 SQL 引擎。"
            "用户提问时，先调用 get_table_schema 查看表结构，"
            "再调用 generate_sql 生成SQL，最后调用 execute_sql 执行。"
            "如果结果包含数据，以 JSON 格式返回：{\"success\": true, \"data\": [...], \"row_count\": N}。"
        ))

    def execute_stream(self, query: str):
        messages = [
            self.system_msg,
            HumanMessage(content=query),
        ]
        while True:
            response = self.llm.bind_tools(self.tools).invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                yield response.content.strip() + "\n"
                break

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_input = tc["args"]
                tool_fn = next((t for t in self.tools if t.name == tool_name), None)
                if not tool_fn:
                    continue
                try:
                    tool_result = tool_fn.invoke(tool_input)
                except Exception as e:
                    tool_result = f"{{\"success\": false, \"error\": \"{e}\"}}"
                messages.append(ToolMessage(content=tool_result, tool_call_id=tc["id"]))

            if len(messages) > 20:
                yield "会话过长，请重新开始"
                break


if __name__ == "__main__":
    agent = ReactAgent()
    for chunk in agent.execute_stream("查询华东地区的销售总额"):
        print(chunk, end="", flush=True)