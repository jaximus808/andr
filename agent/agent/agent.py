"""
agent.py — ROS 2 Action Server that delegates to a LangChain AgentExecutor.

When a goal arrives on the ``agent/prompt`` action, the server builds a
LangChain tool-calling agent from the configured LLM and the robot's skill
tools, then invokes it.  LangChain handles the ReAct loop (LLM → tool → LLM)
internally until the task is solved or an error occurs.

ROS 2 parameters
----------------
llm_backend         string   "ollama"        # "ollama" | "openai"
llm_model           string   ""              # model name (e.g. "llama3", "gpt-4o")
llm_host            string   "http://localhost:11434"
llm_temperature     float    0.2
memory_backend      string   "chroma"
memory_top_k        int      4
max_iterations      int      20
"""

from __future__ import annotations

import logging

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from andr.action import Agent
from andr.srv import GetAgentConfig, SetAgentConfig

from .memory import create_memory, MemoryStore
from .skills import SkillsRegistry, SkillExecutor
from .tools import create_tools_from_registry
from .prompts.system_prompt import DEFAULT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _create_langchain_llm(backend: str, model: str, host: str, temperature: float):
    """Instantiate the appropriate LangChain chat model."""
    if backend == "ollama":
        from langchain_ollama import ChatOllama
        kwargs = {"temperature": temperature, "num_ctx": 2048, "keep_alive": "10m"}
        if model:
            kwargs["model"] = model
        kwargs["base_url"] = host
        return ChatOllama(**kwargs)

    if backend == "openai":
        from langchain_openai import ChatOpenAI
        kwargs = {"temperature": temperature}
        if model:
            kwargs["model"] = model
        return ChatOpenAI(**kwargs)

    raise ValueError(
        f"Unknown llm_backend '{backend}'. Supported: 'ollama', 'openai'"
    )


class AgentServer(Node):
    """ROS 2 action server that runs a LangChain agent per goal."""

    ACTION_NAME = "agent/prompt"

    def __init__(self):
        super().__init__("agent_server")
        self._declare_parameters()
        self._setup_memory()
        self._setup_skills()
        self._setup_langchain()
        self._setup_action_server()
        self._setup_config_services()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _declare_parameters(self) -> None:
        self.declare_parameter("llm_backend",      "ollama")
        self.declare_parameter("llm_model",        "")
        self.declare_parameter("llm_host",         "http://localhost:11434")
        self.declare_parameter("llm_temperature",  0.2)
        self.declare_parameter("memory_backend",   "chroma")
        self.declare_parameter("memory_top_k",     4)
        self.declare_parameter("max_iterations",   20)

    def _setup_memory(self) -> None:
        backend = self._str("memory_backend")
        self._memory_top_k = self.get_parameter("memory_top_k").get_parameter_value().integer_value
        self._memory: MemoryStore = create_memory(backend)
        self.get_logger().info(f"Memory: backend='{backend}' top_k={self._memory_top_k}")

    def _setup_skills(self) -> None:
        self._skills = SkillsRegistry.from_tool_manager(self, timeout_sec=5.0)
        self.get_logger().info(
            f"Skills: discovered {len(self._skills)} tool(s) from tool_manager."
        )
        self._skill_executor = SkillExecutor(self._skills, self)

    def _setup_langchain(self) -> None:
        from langgraph.prebuilt import create_react_agent

        backend     = self._str("llm_backend")
        model       = self._str("llm_model")
        host        = self._str("llm_host")
        temperature = self.get_parameter("llm_temperature").get_parameter_value().double_value

        self.get_logger().info(
            f"Loading LangChain LLM: backend='{backend}' model='{model or '(default)'}' host='{host}'"
        )
        self._llm = _create_langchain_llm(backend, model, host, temperature)

        # Build LangChain tools from the skills registry + RAG
        self._tools = create_tools_from_registry(
            registry=self._skills,
            executor=self._skill_executor,
            memory=self._memory,
            memory_top_k=self._memory_top_k,
        )
        self.get_logger().info(f"LangChain tools: {[t.name for t in self._tools]}")

        # Cache the ReAct agent graph so it's not recreated per goal
        self._agent = create_react_agent(
            self._llm,
            self._tools,
            prompt=DEFAULT_SYSTEM_PROMPT,
        )
        self.get_logger().info("ReAct agent graph cached for reuse.")

    def _setup_action_server(self) -> None:
        self._cb_group = ReentrantCallbackGroup()
        self._action_server = ActionServer(
            self,
            Agent,
            self.ACTION_NAME,
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
            callback_group=self._cb_group,
        )
        self.get_logger().info(f"AgentServer ready — action '{self.ACTION_NAME}'")

    def _setup_config_services(self) -> None:
        self._get_config_srv = self.create_service(
            GetAgentConfig, "agent/get_config", self._handle_get_config,
            callback_group=self._cb_group,
        )
        self._set_config_srv = self.create_service(
            SetAgentConfig, "agent/set_config", self._handle_set_config,
            callback_group=self._cb_group,
        )
        self.get_logger().info("Agent config services ready (agent/get_config, agent/set_config)")

    def _handle_get_config(self, _req, res):
        res.llm_backend = self._str("llm_backend")
        res.llm_model = self._str("llm_model")
        res.llm_host = self._str("llm_host")
        res.llm_temperature = self.get_parameter("llm_temperature").get_parameter_value().double_value
        res.max_iterations = self.get_parameter("max_iterations").get_parameter_value().integer_value
        res.memory_backend = self._str("memory_backend")
        res.memory_top_k = self.get_parameter("memory_top_k").get_parameter_value().integer_value
        return res

    def _handle_set_config(self, req, res):
        """Update agent config params and rebuild the LLM + agent graph."""
        from rclpy.parameter import Parameter

        changes = []

        if req.llm_backend:
            self.set_parameters([Parameter("llm_backend", Parameter.Type.STRING, req.llm_backend)])
            changes.append(f"llm_backend={req.llm_backend}")

        if req.llm_model:
            self.set_parameters([Parameter("llm_model", Parameter.Type.STRING, req.llm_model)])
            changes.append(f"llm_model={req.llm_model}")

        if req.llm_host:
            self.set_parameters([Parameter("llm_host", Parameter.Type.STRING, req.llm_host)])
            changes.append(f"llm_host={req.llm_host}")

        if req.llm_temperature >= 0.0:
            self.set_parameters([Parameter("llm_temperature", Parameter.Type.DOUBLE, req.llm_temperature)])
            changes.append(f"llm_temperature={req.llm_temperature}")

        if req.max_iterations > 0:
            self.set_parameters([Parameter("max_iterations", Parameter.Type.INTEGER, req.max_iterations)])
            changes.append(f"max_iterations={req.max_iterations}")

        if not changes:
            res.success = True
            res.message = "No changes requested."
            return res

        try:
            self._setup_langchain()
            res.success = True
            res.message = f"Updated: {', '.join(changes)}. LLM rebuilt."
            self.get_logger().info(f"Config updated: {', '.join(changes)}")
        except Exception as exc:
            res.success = False
            res.message = f"Failed to rebuild LLM after config change: {exc}"
            self.get_logger().error(res.message)

        return res

    def destroy(self):
        self._action_server.destroy()
        super().destroy_node()

    def _goal_cb(self, goal_request) -> GoalResponse:
        if not goal_request.prompt.strip():
            self.get_logger().warn("Rejecting goal: empty prompt.")
            return GoalResponse.REJECT
        self.get_logger().info(f"Accepting goal — prompt: '{goal_request.prompt[:80]}'")
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        self.get_logger().info(f"Cancel requested for goal {goal_handle.goal_id}.")
        return CancelResponse.ACCEPT

    # ------------------------------------------------------------------
    # Execute callback — invokes LangChain AgentExecutor
    # ------------------------------------------------------------------

    def _execute_cb(self, goal_handle) -> Agent.Result:
        """Invoke the cached LangChain agent for this goal."""
        from langchain_core.messages import HumanMessage

        goal: Agent.Goal = goal_handle.request
        result = Agent.Result()
        max_iter = self.get_parameter("max_iterations").get_parameter_value().integer_value

        self.get_logger().info(f"Starting LangChain agent — prompt: '{goal.prompt[:80]}'")

        # If runtime context is provided, prepend it to the user prompt
        user_content = goal.prompt
        if goal.context:
            user_content = f"[RUNTIME CONTEXT: {goal.context}]\n\n{goal.prompt}"

        # Send initial feedback
        self._send_feedback(goal_handle, 1, "thinking", "Invoking LangChain agent…", 0.1)

        try:
            import time as _time
            from langchain_core.messages import AIMessage, ToolMessage

            # Stream through the ReAct loop so we can log each step
            self.get_logger().info("Sending prompt to LLM — waiting for response…")
            t0 = _time.monotonic()
            step = 0
            response = None

            for event in self._agent.stream(
                {"messages": [HumanMessage(content=user_content)]},
                config={"recursion_limit": max_iter},
                stream_mode="updates",
            ):
                elapsed = _time.monotonic() - t0
                step += 1

                for _, update in event.items():
                    msgs = update.get("messages", [])
                    for msg in msgs:
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    self.get_logger().info(
                                        f"[{elapsed:.1f}s] LLM decided to call tool: "
                                        f"'{tc['name']}' args={tc['args']}"
                                    )
                                    self._send_feedback(
                                        goal_handle, step, "tool_call",
                                        f"Calling {tc['name']}…", 0.3,
                                    )
                            elif msg.content:
                                self.get_logger().info(
                                    f"[{elapsed:.1f}s] LLM response: {msg.content[:150]}"
                                )
                        elif isinstance(msg, ToolMessage):
                            self.get_logger().info(
                                f"[{elapsed:.1f}s] Tool '{msg.name}' returned: "
                                f"{msg.content[:120]}"
                            )
                            self._send_feedback(
                                goal_handle, step, "tool_result",
                                f"{msg.name} done", 0.6,
                            )

                    # Keep last update as response
                    response = update

            total = _time.monotonic() - t0
            self.get_logger().info(f"[{total:.1f}s] ReAct loop finished ({step} steps)")

            # Extract final output from the last message
            if response and "messages" in response:
                output = response["messages"][-1].content
            else:
                output = "Agent completed but produced no output."

            self.get_logger().info(f"Agent completed: {output[:200]}")
            self._send_feedback(goal_handle, 2, "done", output, 1.0)
            goal_handle.succeed()
            result.success = True
            result.summary = output
            return result

        except Exception as exc:
            error_msg = f"Agent error: {exc}"
            self.get_logger().error(error_msg)
            self._send_feedback(goal_handle, 2, "failed", error_msg, 1.0)
            goal_handle.abort()
            result.success = False
            result.summary = error_msg
            return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_feedback(self, goal_handle, iteration, state, status, progress) -> None:
        fb = Agent.Feedback()
        fb.iteration = iteration
        fb.state     = state
        fb.status    = status
        fb.progress  = float(max(0.0, min(1.0, progress)))
        goal_handle.publish_feedback(fb)

    def _str(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value


def main(args=None):
    logging.basicConfig(level=logging.DEBUG)
    rclpy.init(args=args)
    server = AgentServer()
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.destroy()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
