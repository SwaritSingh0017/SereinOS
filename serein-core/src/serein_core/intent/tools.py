"""Tool registration and execution primitives for Serein."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    requires_confirmation: bool = False

    async def execute(self, **kwargs: Any) -> Any:
        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass(frozen=True)
class ToolResult:
    success: bool
    result: Any = None
    error: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        *,
        requires_confirmation: bool = False,
    ) -> Tool:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        tool = Tool(name, description, handler, requires_confirmation)
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def list(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())

    async def execute(
        self,
        name: str,
        *,
        confirmation: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        tool = self.get(name)
        if tool.requires_confirmation and not confirmation:
            return ToolResult(False, error=f"Confirmation required for {name}")
        try:
            return ToolResult(True, result=await tool.execute(**kwargs))
        except Exception as exc:
            return ToolResult(False, error=str(exc))


def tool(
    name: str,
    description: str,
    *,
    requires_confirmation: bool = False,
):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            func,
            "__serein_tool__",
            Tool(name, description, func, requires_confirmation),
        )
        return func
    return decorator
