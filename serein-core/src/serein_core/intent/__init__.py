"""Serein Intent System.

Transforms natural-language input into a stable, structured description of
what the user wants. It never executes operating-system actions itself.
"""

from .classifier import IntentClassifier
from .schemas import Entity, IntentKind, IntentResult, IntentSchema, ToolSchema
from .tools import Tool, ToolRegistry, ToolResult, tool

__all__ = [
    "IntentClassifier",
    "IntentKind",
    "IntentResult",
    "IntentSchema",
    "Entity",
    "ToolRegistry",
    "Tool",
    "ToolResult",
    "tool",
    "ToolSchema",
]
