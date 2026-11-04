"""Deterministic, local-first intent recognition for Serein.

This module intentionally has no network dependency.  It performs fast
classification and entity extraction that can be passed to the Decision
Engine.  A future Brain adapter may enrich an ambiguous result, but the
classifier remains a safe deterministic fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .schemas import IntentResult, IntentSchema, IntentKind


DEFAULT_INTENTS = (
    IntentSchema("greeting", "User is greeting Serein.", ("hi", "hello", "hey")),
    IntentSchema("farewell", "User is ending the conversation.", ("bye", "goodbye")),
    IntentSchema("reminder", "Create or manage a reminder or alarm.", ("remind me", "set an alarm")),
    IntentSchema("system_control", "Control the operating system.", ("shutdown", "restart", "sleep")),
    IntentSchema("file_operation", "Create, move, copy, rename, or delete files.", ("move file", "delete file")),
    IntentSchema("app_control", "Open, close, or manage an application.", ("open chrome", "close spotify")),
    IntentSchema("search", "Search the web or local information source.", ("search for", "look up")),
    IntentSchema("question", "Ask Serein for information or an explanation.", ("what", "why", "how")),
    IntentSchema("conversation", "General conversation.", ("tell me", "talk")),
)


@dataclass(frozen=True)
class _Candidate:
    intent: str
    confidence: float
    entities: dict[str, Any]
    confirmation: bool
    reason: str


class IntentClassifier:
    """Fast local classifier with normalized entities and safe confidence."""

    _APP_RE = re.compile(r"^(?:open|launch|start|close|quit|exit|kill)\s+(.+?)(?:\s+for me)?[.!?]*$", re.I)
    _FILE_RE = re.compile(r"\b(?:file|folder|directory)\b", re.I)
    _TIME_RE = re.compile(r"\b(?:at|by|for)\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b", re.I)
    _DATE_RE = re.compile(r"\b(?:(?:on|for)\s+)?((?:today|tomorrow|tonight)|(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?)\b", re.I)

    def __init__(self, intents: Iterable[IntentSchema] = DEFAULT_INTENTS) -> None:
        self.intents = tuple(intents)
        self._names = {item.name for item in self.intents}

    def classify(self, text: str) -> IntentResult:
        raw = str(text or "").strip()
        normalized = self._normalize(raw)
        if not normalized:
            return IntentResult(IntentKind.UNKNOWN.value, 0.0, raw_text=raw, normalized_text="", reason="empty input")

        candidates = [
            self._greeting(normalized),
            self._farewell(normalized),
            self._reminder(normalized),
            self._file_operation(normalized),
            self._system_control(normalized),
            self._app_control(normalized),
            self._search(normalized),
            self._question(normalized),
        ]
        candidates = [c for c in candidates if c is not None]

        if not candidates:
            candidate = _Candidate(IntentKind.CONVERSATION.value, 0.55, {}, False, "no stronger action or question pattern matched")
        else:
            candidate = max(candidates, key=lambda c: c.confidence)

        if candidate.intent not in self._names and candidate.intent not in {x.value for x in IntentKind}:
            candidate = _Candidate(IntentKind.UNKNOWN.value, 0.0, {}, False, "intent is not registered")

        return IntentResult(
            intent=candidate.intent,
            confidence=round(max(0.0, min(1.0, candidate.confidence)), 3),
            entities=candidate.entities,
            requires_confirmation=candidate.confirmation,
            raw_text=raw,
            normalized_text=normalized,
            reason=candidate.reason,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    def _greeting(self, text: str) -> _Candidate | None:
        if re.match(r"^(hi|hello|hey|good morning|good afternoon|good evening|namaste)\b", text):
            return _Candidate("greeting", 0.99, {}, False, "greeting pattern")
        return None

    def _farewell(self, text: str) -> _Candidate | None:
        if re.match(r"^(bye|goodbye|see you|good night)\b", text):
            return _Candidate("farewell", 0.99, {}, False, "farewell pattern")
        return None

    def _reminder(self, text: str) -> _Candidate | None:
        if not re.search(r"\b(remind me|set (?:an? )?alarm|set (?:an? )?reminder|reminder|alarm)\b", text):
            return None
        entities: dict[str, Any] = {}
        time_match = self._TIME_RE.search(text)
        date_match = self._DATE_RE.search(text)
        if time_match:
            entities["time"] = re.sub(r"\s+", " ", time_match.group(1)).strip()
        if date_match:
            entities["date"] = date_match.group(1)
        cleaned = re.sub(r"\b(?:remind me|set (?:an? )?alarm|set (?:an? )?reminder)\b", "", text).strip(" .!? ")
        cleaned = re.sub(r"\b(?:at|by|for)\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\b(?:today|tomorrow|tonight|(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?)\b", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .!? ")
        if cleaned:
            entities["task"] = cleaned
        confidence = 0.97 if "time" in entities else 0.91
        return _Candidate("reminder", confidence, entities, False, "reminder/alarm pattern")

    def _file_operation(self, text: str) -> _Candidate | None:
        operations = ("delete", "remove", "move", "copy", "rename", "create", "make")
        operation = next((op for op in operations if re.search(rf"\b{op}\b", text)), None)
        if operation is None or not self._FILE_RE.search(text):
            return None
        entities = {"operation": operation}
        confirmation = operation in {"delete", "remove"}
        return _Candidate("file_operation", 0.96, entities, confirmation, "file operation pattern")

    def _system_control(self, text: str) -> _Candidate | None:
        patterns = {
            "shutdown": ("shutdown", "shut down", "power off"),
            "restart": ("restart", "reboot"),
            "sleep": ("put the computer to sleep", "sleep the computer"),
            "lock": ("lock the computer", "lock my pc", "lock my computer"),
        }
        for action, phrases in patterns.items():
            if any(p in text for p in phrases):
                return _Candidate("system_control", 0.98, {"action": action}, True, "system control pattern")
        return None

    def _app_control(self, text: str) -> _Candidate | None:
        match = self._APP_RE.match(text)
        if not match:
            return None
        action = text.split(maxsplit=1)[0]
        app = match.group(1).strip(" .!? ")
        if not app or app in {"me", "it"}:
            return None
        return _Candidate("app_control", 0.94, {"action": action, "application": app}, action.lower() in {"kill", "quit", "exit"}, "application control pattern")

    def _search(self, text: str) -> _Candidate | None:
        match = re.match(r"^(?:search(?: for)?|look up|find)\s+(.+)$", text)
        if match:
            return _Candidate("search", 0.92, {"query": match.group(1).strip(" .!? ")}, False, "search pattern")
        return None

    def _question(self, text: str) -> _Candidate | None:
        if text.endswith("?") or re.match(r"^(what|why|how|when|where|who|which|can|could|is|are|do|does|did|will)\b", text):
            return _Candidate("question", 0.82, {}, False, "question pattern")
        return None
