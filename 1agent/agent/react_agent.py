from langchain.agents import create_agent
from model.factory import chat_model
from utils.prompt_loader import load_system_prompts
from agent.tools.agent_tools import generate_sql, get_table_schema, execute_sql, execute_excel
from agent.tools.middleware import monitor_tool, log_before_model


class ReactAgent:
    def __init__(self):
        self.agent = create_agent(
            model=chat_model,
            system_prompt=load_system_prompts(),
            tools=[generate_sql, get_table_schema, execute_sql, execute_excel],
            middleware=[monitor_tool, log_before_model],
        )
        self.max_history = 20
        self.history: list[dict] = []
        self.last_user_query: str = ""

    def execute_stream(self, query: str):
        if len(self.history) >= self.max_history:
            yield "会话历史已达上限(20条)，请清除上下文后重新开始。\n"
            return

        # 拼接历史 + 上一轮用户需求 + 新用户问题
        messages = list(self.history)
        if self.last_user_query:
            messages.append({
                "role": "system",
                "content": f"上一轮用户需求: {self.last_user_query}"
            })
        messages.append({"role": "user", "content": query})
        self.last_user_query = query
        input_dict = {"messages": messages}

        response_messages = list(messages)
        for chunk in self.agent.stream(input_dict, stream_mode="values"):
            latest = chunk["messages"][-1]
            if latest.content:
                yield latest.content.strip() + "\n"
            response_messages = chunk["messages"]

        # 只保留 user + 纯文本 assistant（不要 tool 相关）
        clean = []
        for m in response_messages[1:]:
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "type", "")
            if isinstance(m, dict):
                role = m.get("role", "")
                has_tc = bool(m.get("tool_calls"))
            elif hasattr(m, "type"):
                role = m.type
                has_tc = bool(getattr(m, "tool_calls", None))
            else:
                has_tc = False
            if role in ("user", "human"):
                clean.append(m)
            elif role in ("ai", "assistant") and not has_tc:
                clean.append(m)
        self.history = clean
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def clear_history(self):
        self.history = []
        self.last_user_query = ""