"""Registry of every tool, in the order shown to the model."""
from __future__ import annotations

from . import tools_chem, tools_lookup, tools_open, tools_run
from .toolkit import Tool


def all_tools() -> list[Tool]:
    tools = [*tools_lookup.TOOLS, *tools_chem.TOOLS, *tools_open.TOOLS, *tools_run.TOOLS]
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate tool name")
    return tools
