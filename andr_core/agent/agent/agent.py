"""
agent.py — ROS 2 Action Server with a custom ReAct loop.

When a goal arrives on the ``agent/prompt`` action, the server runs a
manual ReAct loop: send prompt to LLM → detect tool calls (structured or
raw JSON) → execute via tool_manager → feed result back → repeat.

The agent dynamically discovers available tools by calling the
``tool_manager/list`` service at startup.

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

import json
import logging

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from andr_msgs.action import Agent
from andr_msgs.srv import GetAgentConfig, SetAgentConfig, GetSystemPrompt

from .memory import create_memory, MemoryStore
from .skills import SkillsRegistry, SkillExecutor
from .tools import create_tools_from_registry
from .prompts.system_prompt import DEFAULT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _parse_raw_tool_call(text: str) -> dict | None:
    """Try to extract a tool call from raw LLM text output.

    Handles formats like:
      {"name": "tool_name", "parameters": {...}}
      {"name": "tool_name", "arguments": {...}}
      {"tool": "tool_name", "params": {...}}
    Returns {"name": str, "args": dict} or None.
    """
    text = text.strip()

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    json_str = text[start:end + 1]
    try:
        obj = json.loads(json_str)
    except json.JSONDecodeError:
        logger.debug("_parse_raw_tool_call: JSON parse failed for: %s", json_str[:100])
        return None

    if not isinstance(obj, dict):
        return None

    # Extract tool name
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    if not name:
        return None

    # Extract arguments
    args = (
        obj.get("parameters")
        or obj.get("arguments")
        or obj.get("params")
        or obj.get("args")
        or {}
    )

    logger.info("_parse_raw_tool_call: detected tool='%s' args=%s", name, args)
    return {"name": str(name), "args": args if isinstance(args, dict) else {}}


def _create_langchain_llm(backend: str, model: str, host: str, temperature: float):
    """Instantiate the appropriate LangChain chat model."""
    if not model:
        raise ValueError(
            "No model specified. Set 'model' in andr.config.yaml or pass --model on the CLI."
        )

    if backend == "ollama":
        from langchain_ollama import ChatOllama
        kwargs = {
            "model": model,
            "temperature": temperature,
            "num_ctx": 8192,
            "keep_alive": "10m",
            "base_url": host,
        }
        return ChatOllama(**kwargs)

    if backend == "openai":
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "temperature": temperature}
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
        self._system_prompt = self._fetch_system_prompt()
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
        print("Discovering skills from tool_manager…")
        self._skills = SkillsRegistry.from_tool_manager(self, timeout_sec=10.0)
        self.get_logger().info(
            f"Skills: discovered {len(self._skills)} tool(s) from tool_manager: "
            f"{self._skills.names()}"
        )
        self._skill_executor = SkillExecutor(self._skills, self)

    def _fetch_system_prompt(self) -> str:
        """Fetch the active system prompt from prompt_manager, fall back to hardcoded default."""
        client = self.create_client(GetSystemPrompt, "prompt_manager/get_system_prompt")

        self.get_logger().info("Waiting for prompt_manager/get_system_prompt service…")
        if not client.wait_for_service(timeout_sec=10.0):
            self.get_logger().warn(
                "prompt_manager not available — using hardcoded default system prompt."
            )
            return DEFAULT_SYSTEM_PROMPT

        future = client.call_async(GetSystemPrompt.Request())
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)

        if future.result() is None or not future.result().success:
            self.get_logger().warn("Failed to fetch system prompt — using hardcoded default.")
            return DEFAULT_SYSTEM_PROMPT

        prompt = future.result().prompt
        version = future.result().version
        self.get_logger().info(f"Loaded system prompt version {version} from prompt_manager")
        return prompt

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
        self._tools_by_name = {t.name: t for t in self._tools}
        self.get_logger().info(f"LangChain tools: {list(self._tools_by_name.keys())}")

        # Bind tools to LLM (for models that support structured tool calling)
        # If the model doesn't support it, we fall back to parsing raw JSON
        try:
            self._llm_with_tools = self._llm.bind_tools(self._tools)
            self.get_logger().info("Tools bound to LLM (structured tool calling enabled).")
        except Exception as exc:
            self.get_logger().warn(
                f"Could not bind tools to LLM ({exc}). "
                "Will rely on raw JSON parsing for tool calls."
            )
            self._llm_with_tools = self._llm

        # Preload the model into GPU memory via Ollama's load API
        if backend == "ollama":
            self._warmup_ollama(host, model)

        self.get_logger().info("Agent ready (custom ReAct loop with JSON fallback).")

    def _warmup_ollama(self, host: str, model: str) -> None:
        """Preload the Ollama model into GPU memory by running a trivial generation."""
        import urllib.request
        import json

        url = f"{host.rstrip('/')}/api/generate"
        payload = json.dumps({
            "model": model,
            "prompt": "hi",
            "keep_alive": "10m",
            "options": {"num_predict": 1},
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

        self.get_logger().info(f"Preloading model '{model}' into Ollama...")
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            resp.read()  # consume streaming response to ensure model is fully loaded
            self.get_logger().info("Model preloaded.")
        except Exception as exc:
            self.get_logger().warn(f"Ollama preload failed (non-fatal): {exc}")

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
    # Execute callback — custom ReAct loop with JSON fallback
    # ------------------------------------------------------------------

    def _execute_cb(self, goal_handle) -> Agent.Result:
        """Run a ReAct loop: LLM → tool call (structured or raw JSON) → repeat."""
        import time as _time
        from langchain_core.messages import (
            AIMessage, HumanMessage, SystemMessage, ToolMessage,
        )

        goal: Agent.Goal = goal_handle.request
        result = Agent.Result()
        max_iter = self.get_parameter("max_iterations").get_parameter_value().integer_value

        self.get_logger().info(f"Starting agent — prompt: '{goal.prompt[:80]}'")

        # Refresh tools from tool_manager so we always have the live set
        new_names = self._skills.refresh()
        if new_names:
            self.get_logger().info(f"Discovered new tools before execution: {new_names}")
            self._rebuild_tools()

        # Build the message history
        user_content = goal.prompt
        if goal.context:
            user_content = f"[RUNTIME CONTEXT: {goal.context}]\n\n{goal.prompt}"

        # Build tool descriptions for the system prompt
        tool_desc_lines = []
        for t in self._tools:
            params = ""
            if hasattr(t, "args_schema") and t.args_schema:
                schema = t.args_schema.schema()
                props = schema.get("properties", {})
                required = set(schema.get("required", []))
                param_parts = []
                for pname, pinfo in props.items():
                    req_mark = " (required)" if pname in required else " (optional)"
                    param_parts.append(f"    - {pname}: {pinfo.get('type', 'string')}{req_mark} — {pinfo.get('description', '')}")
                params = "\n".join(param_parts)
            tool_desc_lines.append(f"  {t.name}: {t.description}")
            if params:
                tool_desc_lines.append(params)

        tool_block = "\n".join(tool_desc_lines)

        system_content = (
            f"{self._system_prompt}\n\n"
            f"=== AVAILABLE TOOLS ===\n{tool_block}\n\n"
            f"To call a tool, respond with ONLY a JSON object in this exact format:\n"
            f'{{"name": "<tool_name>", "parameters": {{...}}}}\n\n'
            f"Do NOT include any other text when calling a tool — just the JSON object.\n"
            f"When you have a final answer (no tool needed), respond with plain text."
        )

        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=user_content),
        ]

        self._send_feedback(goal_handle, 1, "thinking", "Processing…", 0.1)
        t0 = _time.monotonic()

        try:
            for iteration in range(1, max_iter + 1):
                elapsed = _time.monotonic() - t0
                self.get_logger().info(f"[{elapsed:.1f}s] ReAct iteration {iteration}/{max_iter}")

                # Call the LLM
                ai_response = self._llm_with_tools.invoke(messages)

                # Check for structured tool calls first (models that support it)
                if hasattr(ai_response, "tool_calls") and ai_response.tool_calls:
                    messages.append(ai_response)
                    for tc in ai_response.tool_calls:
                        tool_name = tc["name"]
                        tool_args = tc["args"]
                        elapsed = _time.monotonic() - t0
                        self.get_logger().info(
                            f"[{elapsed:.1f}s] Structured tool call: '{tool_name}' args={tool_args}"
                        )
                        self._send_feedback(
                            goal_handle, iteration, "tool_call",
                            f"Calling {tool_name}…", 0.3,
                        )

                        # Execute through the tool (which goes through SkillExecutor → tool_manager)
                        tool_result = self._execute_tool(tool_name, tool_args)
                        elapsed = _time.monotonic() - t0
                        self.get_logger().info(
                            f"[{elapsed:.1f}s] Tool '{tool_name}' returned: {tool_result[:120]}"
                        )
                        self._send_feedback(
                            goal_handle, iteration, "tool_result",
                            f"{tool_name}: {tool_result[:80]}", 0.6,
                        )

                        messages.append(ToolMessage(
                            content=tool_result,
                            tool_call_id=tc.get("id", f"call_{iteration}"),
                        ))
                    continue

                # No structured tool call — check for raw JSON tool call in text
                content = ai_response.content if hasattr(ai_response, "content") else str(ai_response)
                elapsed = _time.monotonic() - t0
                self.get_logger().info(f"[{elapsed:.1f}s] LLM text: {content[:200]}")

                parsed = _parse_raw_tool_call(content)

                if parsed:
                    tool_name = parsed["name"]
                    tool_args = parsed["args"]
                    self.get_logger().info(
                        f"[{elapsed:.1f}s] Parsed raw JSON tool call: "
                        f"'{tool_name}' args={tool_args}"
                    )
                    self._send_feedback(
                        goal_handle, iteration, "tool_call",
                        f"Calling {tool_name}…", 0.3,
                    )

                    # Execute through the tool → SkillExecutor → tool_manager
                    tool_result = self._execute_tool(tool_name, tool_args)
                    elapsed = _time.monotonic() - t0
                    self.get_logger().info(
                        f"[{elapsed:.1f}s] Tool '{tool_name}' returned: {tool_result[:120]}"
                    )
                    self._send_feedback(
                        goal_handle, iteration, "tool_result",
                        f"{tool_name}: {tool_result[:80]}", 0.6,
                    )

                    # Add tool call + result to history so LLM sees what happened
                    messages.append(AIMessage(content=content))
                    messages.append(HumanMessage(
                        content=f"Tool '{tool_name}' returned: {tool_result}\n\n"
                        f"Now provide your response to the user based on this result."
                    ))
                    continue

                # No tool call detected — this is the final answer
                total = _time.monotonic() - t0
                self.get_logger().info(
                    f"[{total:.1f}s] Agent finished ({iteration} iterations): {content[:200]}"
                )
                self._send_feedback(goal_handle, iteration, "done", content, 1.0)
                goal_handle.succeed()
                result.success = True
                result.summary = content
                return result

            # Max iterations reached
            total = _time.monotonic() - t0
            output = f"Agent reached max iterations ({max_iter}) without a final answer."
            self.get_logger().warn(f"[{total:.1f}s] {output}")
            self._send_feedback(goal_handle, max_iter, "done", output, 1.0)
            goal_handle.succeed()
            result.success = True
            result.summary = output
            return result

        except Exception as exc:
            error_msg = f"Agent error: {exc}"
            self.get_logger().error(error_msg)
            self._send_feedback(goal_handle, 1, "failed", error_msg, 1.0)
            goal_handle.abort()
            result.success = False
            result.summary = error_msg
            return result

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Execute a tool by name.

        If the tool exists in the local LangChain cache, use it (goes through
        the ROS2SkillTool wrapper).  Otherwise, dispatch directly to the
        tool_manager/execute action server — the tool_manager is the real
        source of truth for what tools are registered.
        """
        tool = self._tools_by_name.get(tool_name)
        if tool is not None:
            try:
                return tool.invoke(args)
            except Exception as exc:
                return f"ERROR: Tool '{tool_name}' failed: {exc}"

        # Tool not in local cache — send directly to tool_manager.
        # This handles the common case where tool servers registered after
        # the agent built its LangChain tool list.
        self.get_logger().info(
            f"Tool '{tool_name}' not in local cache — dispatching directly "
            f"to tool_manager/execute"
        )
        return self._skill_executor.execute_unchecked(tool_name, args)

    def _rebuild_tools(self) -> None:
        """Rebuild LangChain tools from the (possibly refreshed) skills registry."""
        self._tools = create_tools_from_registry(
            registry=self._skills,
            executor=self._skill_executor,
            memory=self._memory,
            memory_top_k=self._memory_top_k,
        )
        self._tools_by_name = {t.name: t for t in self._tools}
        self.get_logger().info(f"Rebuilt LangChain tools: {list(self._tools_by_name.keys())}")

        # Re-bind tools to LLM for structured tool calling
        try:
            self._llm_with_tools = self._llm.bind_tools(self._tools)
        except Exception:
            self._llm_with_tools = self._llm

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
