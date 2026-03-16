"""tools.py — LangChain tool wrappers that bridge to ROS 2 skill execution.

Each skill defined in skills.yaml becomes a LangChain BaseTool with a proper
description and dynamically-generated Pydantic args schema.  When invoked by the
LangChain AgentExecutor, the tool dispatches the call through SkillExecutor
(real ROS 2 action client or mock fallback).

A ``query_knowledge_base`` tool is also provided for RAG lookups.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from .skills import Skill, SkillsRegistry, SkillExecutor
from .memory import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type mapping: skills.yaml type strings → Python types
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "float":  float,
    "bool":   bool,
    "int":    int,
    "array":  list,
}


def _build_args_schema(skill: Skill) -> Type[BaseModel]:
    """Dynamically create a Pydantic model from a Skill's parameter list."""
    fields: dict[str, Any] = {}
    for p in skill.parameters:
        py_type = _TYPE_MAP.get(p.type, str)
        if p.required:
            fields[p.name] = (py_type, Field(description=p.description))
        else:
            fields[p.name] = (Optional[py_type], Field(default=None, description=p.description))

    model = create_model(f"{skill.name}_args", **fields)
    return model


# ---------------------------------------------------------------------------
# ROS2SkillTool — one instance per skill
# ---------------------------------------------------------------------------

class ROS2SkillTool(BaseTool):
    """LangChain tool that dispatches to a ROS 2 skill via SkillExecutor."""

    skill_name: str
    executor: Any = None  # SkillExecutor — stored as Any for pydantic compat

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, skill: Skill, executor: SkillExecutor, **kwargs: Any):
        super().__init__(
            name=skill.name,
            description=skill.description,
            args_schema=_build_args_schema(skill),
            skill_name=skill.name,
            executor=executor,
            **kwargs,
        )

    def _run(self, **kwargs: Any) -> str:
        # Strip None values (optional params not provided)
        args = {k: v for k, v in kwargs.items() if v is not None}
        result = self.executor.execute(self.skill_name, args)
        return result


# ---------------------------------------------------------------------------
# RAG / Knowledge-base tool
# ---------------------------------------------------------------------------

class QueryKnowledgeBaseTool(BaseTool):
    """LangChain tool for RAG memory lookups."""

    name: str = "query_knowledge_base"
    description: str = (
        "Retrieves relevant knowledge, context, or past experience from the "
        "robot's memory store. Use this when you need background information, "
        "curriculum data, or spatial/object knowledge before acting."
    )

    memory: Any = None   # MemoryStore — stored as Any for pydantic compat
    top_k: int = 4

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, memory: MemoryStore, top_k: int = 4, **kwargs: Any):
        super().__init__(memory=memory, top_k=top_k, **kwargs)

    def _run(self, query: str) -> str:
        results = self.memory.query(query, top_k=self.top_k)
        if not results:
            return f"No relevant memory found for: '{query}'"
        return self.memory.to_prompt_block(results)


# ---------------------------------------------------------------------------
# Factory: registry → list of LangChain tools
# ---------------------------------------------------------------------------

def create_tools_from_registry(
    registry: SkillsRegistry,
    executor: SkillExecutor,
    memory: Optional[MemoryStore] = None,
    memory_top_k: int = 4,
) -> list[BaseTool]:
    """Build a list of LangChain tools from the skills registry + optional RAG tool."""
    tools: list[BaseTool] = []

    for skill in registry.all_skills():
        tool = ROS2SkillTool(skill=skill, executor=executor)
        logger.info("Created LangChain tool: '%s'", skill.name)
        tools.append(tool)

    if memory is not None:
        rag_tool = QueryKnowledgeBaseTool(memory=memory, top_k=memory_top_k)
        logger.info("Created LangChain tool: 'query_knowledge_base'")
        tools.append(rag_tool)

    return tools
