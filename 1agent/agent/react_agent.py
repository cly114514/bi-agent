from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from model.factory import chat_model
from utils.prompt_loader import load_system_prompts
from agent.tools.agent_tools import generate_sql, get_table_schema, execute_sql
from agent.tools.middleware import monitor_tool, log_before_model


class ReactAgent:
    def __init__(self):
        system_prompt = load_system_prompts()
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])
        middleware = [m for m in [monitor_tool, log_before_model] if m is not None]
        self.tools = [generate_sql, get_table_schema, execute_sql]
        self.agent = create_tool_calling_agent(
            llm=chat_model,
            tools=self.tools,
            prompt=prompt,
        )

    def execute_stream(self, query: str):
        result = self.agent.invoke({
            "input": query,
            "chat_history": [],
            "agent_scratchpad": [],
        })
        msgs = result.get("messages", [])
        if msgs and msgs[-1].content:
            yield msgs[-1].content.strip() + "\n"


if __name__ == "__main__":
    agent = ReactAgent()
    for chunk in agent.execute_stream("查询华东地区的销售总额"):
        print(chunk, end="", flush=True)
