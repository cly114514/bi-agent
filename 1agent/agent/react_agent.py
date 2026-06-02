from agent.langchain_agent import create_agent, LangChainToolAgent
from agent.tools.agent_tools import generate_sql, get_table_schema, execute_sql, execute_excel


def _bootstrap_sys_path():
    """当作为 `python -m agent.react_agent` 入口运行时, 确保 1agent/ 在 sys.path 上."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


class ReactAgent:
    def __init__(self):
        self._agent = create_agent(
            tools=[generate_sql, get_table_schema, execute_sql, execute_excel],
            system_message=(
                "你是 BI 看板 SQL 引擎。"
                "用户提问时，先调用 get_table_schema 查看表结构，"
                "再调用 generate_sql 生成SQL，最后调用 execute_sql 执行。"
                "如果MySQL未连接，调用 execute_excel 直接查询Excel数据。"
                "重要：如果查询成功返回数据，返回 JSON 格式：{\"success\": true, \"data\": [...], \"row_count\": N}。"
            ),
        )

    def execute_stream(self, query: str):
        yield from self._agent.execute_stream(query)


if __name__ == "__main__":
    _bootstrap_sys_path()
    agent = ReactAgent()
    for chunk in agent.execute_stream("查询华东地区的销售总额"):
        print(chunk, end="", flush=True)