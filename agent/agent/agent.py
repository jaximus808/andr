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
skills_yaml         string   ""
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

from .memory import create_memory, MemoryStore
from .skills import SkillsRegistry, SkillExecutor
from .tools import create_tools_from_registry
from .prompts.system_prompt import DEFAULT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _create_langchain_llm(backend: str, model: str, host: str, temperature: float):
    """Instantiate the appropriate LangChain chat model."""
    if backend == "ollama":
        from langchain_ollama import ChatOllama
        kwargs = {"temperature": temperature}
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

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _declare_parameters(self) -> None:
        self.declare_parameter("llm_backend",      "ollama")
        self.declare_parameter("llm_model",        "")
        self.declare_parameter("llm_host",         "http://localhost:11434")
        self.declare_parameter("llm_temperature",  0.2)
        self.declare_parameter("skills_yaml",      "")
        self.declare_parameter("memory_backend",   "chroma")
        self.declare_parameter("memory_top_k",     4)
        self.declare_parameter("max_iterations",   20)

    def _setup_memory(self) -> None:
        backend = self._str("memory_backend")
        self._memory_top_k = self.get_parameter("memory_top_k").get_parameter_value().integer_value
        self._memory: MemoryStore = create_memory(backend)
        self.get_logger().info(f"Memory: backend='{backend}' top_k={self._memory_top_k}")

    def _setup_skills(self) -> None:
        yaml_path = self._str("skills_yaml")
        if yaml_path:
            self._skills = SkillsRegistry.from_yaml(yaml_path)
            self.get_logger().info(
                f"Skills: loaded {len(self._skills)} from '{yaml_path}'"
            )
        else:
            self._skills = SkillsRegistry()
            self.get_logger().info("Skills: no YAML configured — empty registry.")
        self._skill_executor = SkillExecutor(self._skills, self)

    def _setup_langchain(self) -> None:
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
        """Build a LangChain agent and invoke it for this goal."""
        from langgraph.prebuilt import create_react_agent
        from langchain_core.messages import HumanMessage

        goal: Agent.Goal = goal_handle.request
        result = Agent.Result()
        max_iter = self.get_parameter("max_iterations").get_parameter_value().integer_value

        self.get_logger().info(f"Starting LangChain agent — prompt: '{goal.prompt[:80]}'")

        # Build the system prompt
        system_message = DEFAULT_SYSTEM_PROMPT
        if goal.context:
            system_message += f"\n\n=== RUNTIME CONTEXT ===\n{goal.context}"

        # Send initial feedback
        self._send_feedback(goal_handle, 1, "thinking", "Invoking LangChain agent…", 0.1)

        try:
            # Create a react agent graph per-goal
            agent = create_react_agent(
                self._llm,
                self._tools,
                prompt=system_message,
            )

            # Run the agent — langgraph handles the ReAct loop internally
            response = agent.invoke(
                {"messages": [HumanMessage(content=goal.prompt)]},
                config={"recursion_limit": max_iter},
            )
            output = response["messages"][-1].content

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
