from langchain.agents import create_react_agent
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model
from utils.prompt_loader import load_system_prompts
from agent.tools.agent_tools import generate_sql, get_table_schema, execute_sql
from agent.tools.middleware import monitor_tool, log_before_model


class ReactAgent:
    def __init__(self):
        system_prompt = load_system_prompts()
        prompt = PromptTemplate.from_template(
            system_prompt
            + "\n\n"
            + "工具: {tools}\n工具名称: {tool_names}\n{agent_scratchpad}\n问题: {input}\n回答:"
        )
        middleware = [m for m in [monitor_tool, log_before_model] if m is not None]
        self.agent = create_react_agent(
            llm=chat_model,
            tools=[generate_sql, get_table_schema, execute_sql],
            prompt=prompt,
        )

    def execute_stream(self, query: str):
        input_dict = {
            "input": query,
            "tools": [],
            "tool_names": [],
            "agent_scratchpad": [],
            "intermediate_steps": [],
        }
        # Use ainvoke (async invoke) and iterate - works without stream_mode
        result = self.agent.invoke(input_dict)
        msgs = result.get("messages", [])
        if msgs and msgs[-1].content:
            yield msgs[-1].content.strip() + "\n"


if __name__ == "__main__":
    agent = ReactAgent()
    for chunk in agent.execute_stream("查询华东地区的销售总额"):
        print(chunk, end="", flush=True)
