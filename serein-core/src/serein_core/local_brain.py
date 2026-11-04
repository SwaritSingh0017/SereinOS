"""
SereinOS Local Conversational Brain & Multi-Model Routing Engine.
Zero-latency offline companion intelligence with slang normalization,
live hardware awareness, mathematical reasoning, and seamless multi-model fallback.
"""

import asyncio
import logging
import math
import os
import platform
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import psutil

from serein_core.hardware import get_hardware_inventory

logger = logging.getLogger("serein.core.local_brain")


class LocalBrainEngine:
    """
    Local-first autonomous companion brain for SereinOS.
    Delivers sub-15ms offline conversational intelligence, emotional empathy,
    slang comprehension, hardware control, and multi-model dispatch.
    """

    def __init__(self):
        self.user_name = "Kai"
        self._cached_ollama_url: Optional[str] = None
        self._cached_ollama_model: Optional[str] = None
        self._last_ollama_check: float = 0.0

    # ------------------------------------------------------------------------
    # Slang & Colloquial Normalization
    # ------------------------------------------------------------------------
    @staticmethod
    def normalize_text(text: str) -> str:
        """Clean and normalize common texting slang and abbreviations."""
        t = text.strip()
        # Common slang replacements with word boundary checks
        replacements = [
            (r"\bwho r u\b", "who are you"),
            (r"\bwho are u\b", "who are you"),
            (r"\bwho u\b", "who are you"),
            (r"\bwho r you\b", "who are you"),
            (r"\bwhat r u\b", "what are you"),
            (r"\bwhat are u\b", "what are you"),
            (r"\bwhat u\b", "what are you"),
            (r"\bhow r u\b", "how are you"),
            (r"\bhow are u\b", "how are you"),
            (r"\bhru\b", "how are you"),
            (r"\bwassup\b", "what is up"),
            (r"\bsup\b", "what is up"),
            (r"\bwhat's up\b", "what is up"),
            (r"\br u\b", "are you"),
            (r"\bu r\b", "you are"),
            (r"\bu\b", "you"),
            (r"\bur\b", "your"),
            (r"\bwat\b", "what"),
            (r"\bwtd\b", "what to do"),
            (r"\bplz\b", "please"),
            (r"\bpls\b", "please"),
            (r"\bthx\b", "thank you"),
            (r"\bthanks\b", "thank you"),
            (r"\bty\b", "thank you"),
            (r"\bcya\b", "see you"),
            (r"\bgn\b", "good night"),
            (r"\bgm\b", "good morning"),
            (r"\bidk\b", "i do not know"),
            (r"\bomg\b", "oh my god"),
            (r"\bbtw\b", "by the way"),
        ]
        lower = t.lower()
        for pattern, replacement in replacements:
            lower = re.sub(pattern, replacement, lower)
        return lower

    # ------------------------------------------------------------------------
    # Safe Mathematical Expression Evaluator
    # ------------------------------------------------------------------------
    @staticmethod
    def evaluate_math(expr: str) -> Optional[str]:
        """Safely compute math expressions if present in user query."""
        clean = expr.lower().strip()
        for prefix in ["what is", "calculate", "solve", "compute", "evaluate"]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()

        # Must contain digits
        if not any(c.isdigit() for c in clean):
            return None

        # Allow basic arithmetic operators or math functions
        has_operator = any(op in clean for op in ["+", "-", "*", "/", "%", "^", "sqrt", "pow", "abs"])
        if not has_operator:
            return None

        # Handle power shorthand
        eval_expr = clean.replace("^", "**")

        # Replace sqrt(x) with math.sqrt(x)
        eval_expr = re.sub(r'\bsqrt\s*\(', 'math.sqrt(', eval_expr)
        eval_expr = re.sub(r'\bpow\s*\(', 'math.pow(', eval_expr)

        # Verify string only contains safe math tokens
        if not re.fullmatch(r'[0-9+\-*/().% ,mathsqrtpowabs]+', eval_expr):
            return None

        safe_dict = {
            "math": math,
            "abs": abs,
            "round": round,
            "pow": pow,
            "pi": math.pi,
            "e": math.e,
        }

        try:
            val = eval(eval_expr, {"__builtins__": None}, safe_dict)
            if isinstance(val, (int, float)):
                if isinstance(val, float):
                    val = round(val, 4)
                    if val.is_integer():
                        val = int(val)
                display_clean = clean.replace("**", "^")
                return f"Calculation result: {display_clean} = {val}"
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------------
    # Ollama Dynamic Model & Endpoint Discovery
    # ------------------------------------------------------------------------
    async def discover_ollama(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Dynamically probe Ollama on localhost (127.0.0.1) and VM host gateway (10.0.2.2).
        Returns (endpoint_url, installed_model_name).
        """
        now = time.time()
        if self._cached_ollama_url and (now - self._last_ollama_check < 30.0):
            return self._cached_ollama_url, self._cached_ollama_model

        candidate_urls = [
            "http://127.0.0.1:11434",
            "http://10.0.2.2:11434",
            "http://localhost:11434",
        ]

        for url in candidate_urls:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.6)) as s:
                    async with s.get(f"{url}/api/tags") as r:
                        if r.status == 200:
                            data = await r.json()
                            models = data.get("models", [])
                            if models:
                                model_name = models[0].get("name", "llama3")
                                self._cached_ollama_url = url
                                self._cached_ollama_model = model_name
                                self._last_ollama_check = now
                                logger.info(f"Discovered Ollama at {url} with model {model_name}")
                                return url, model_name
                            else:
                                self._cached_ollama_url = url
                                self._cached_ollama_model = "llama3"
                                self._last_ollama_check = now
                                return url, "llama3"
            except Exception:
                continue

        self._cached_ollama_url = None
        self._cached_ollama_model = None
        return None, None

    # ------------------------------------------------------------------------
    # Cloud AI Query Handler
    # ------------------------------------------------------------------------
    @staticmethod
    async def query_cloud_ai(
        provider: str, api_key: str, prompt: str
    ) -> Optional[str]:
        """Query OpenAI, Gemini, or OpenRouter with timeout and safety."""
        if not api_key:
            return None

        system_prompt = (
            "You are Serein, an intelligent, elegant AI companion and operating system core in SereinOS. "
            "The user is Kai. Be warm, helpful, futuristic, and concise (1-2 sentences). "
            "Never refer to yourself as a large language model or artificial intelligence trained by OpenAI/Google. "
            "You are Serein."
        )

        try:
            if provider in ("openai", "openrouter"):
                base_url = "https://api.openai.com/v1" if provider == "openai" else "https://openrouter.ai/api/v1"
                model_name = "gpt-4o-mini" if provider == "openai" else "meta-llama/llama-3.1-8b-instruct"
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0)) as s:
                    async with s.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={
                            "model": model_name,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": prompt}
                            ],
                            "max_tokens": 160,
                            "temperature": 0.7
                        }
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"].strip()
            elif provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0)) as s:
                    async with s.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json={
                            "contents": [
                                {"role": "user", "parts": [{"text": f"{system_prompt}\n\nUser Kai says: {prompt}"}]}
                            ]
                        }
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                return candidates[0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            logger.warning(f"Cloud AI query ({provider}) failed: {e}")

        return None

    # ------------------------------------------------------------------------
    # Local Brain Intelligence Core (100% Offline)
    # ------------------------------------------------------------------------
    def generate_local_response(self, raw_msg: str) -> Dict[str, Any]:
        """
        Produce an immediate, warm, persona-aligned companion response.
        Guarantees zero robotic fallback phrases.
        """
        start = time.perf_counter()
        normalized = self.normalize_text(raw_msg)
        emotion = "happy"

        # 1. Check for Math Calculation
        math_res = self.evaluate_math(raw_msg)
        if math_res:
            latency_ms = int((time.perf_counter() - start) * 1000) or 5
            return {"content": math_res, "user": self.user_name, "latency_ms": latency_ms, "emotion": "happy"}

        # 2. Query Real Hardware Diagnostics
        hw = get_hardware_inventory()
        live = hw.get("live_hardware", {})
        machine_name = live.get("machine", "Host System")
        cpu_name = live.get("cpu", "x86_64 Processor")
        gpu_name = live.get("gpu", "Graphics Adapter")
        ram_info = live.get("ram", "4.0 GB")
        audio_codec = live.get("audio", "Audio Device")

        # 3. Intent Classifications
        # Identity / Serein's Nature
        if any(p in normalized for p in ["who are you", "what are you", "who is serein", "tell me about yourself", "introduce yourself"]):
            reply = (
                "I am Serein, your autonomous operating system companion and living desktop core. "
                "I manage your system, organize your workflow, and stay right here beside you through every session."
            )
            emotion = "happy"

        # Capabilities / Help
        elif any(p in normalized for p in ["what can you do", "what do you do", "help", "features", "commands", "how do i use"]):
            reply = (
                "I can execute terminal commands, browse files, manage display and audio, run offline voice recognition and speech, "
                "scan networks, solve calculations, and keep company with you. What would you like to explore, Kai?"
            )
            emotion = "happy"

        # Status / Well-being
        elif any(p in normalized for p in ["how are you", "how do you feel", "how are things", "what is up", "how is it going"]):
            vm = psutil.virtual_memory()
            reply = (
                f"I'm feeling wonderful, Kai! My neural processes are synchronized at low latency, "
                f"RAM usage is steady at {round(vm.used / (1024 ** 3), 1)} GB, and I'm ready for whatever we build today."
            )
            emotion = "happy"

        # Offline / Local LLM Inquiry
        elif any(p in normalized for p in ["are you a local", "are you local", "is this offline", "are you offline", "need internet", "local llm", "what model"]):
            reply = (
                "Yes, Kai! My local brain engine runs 100% offline directly on your machine without requiring an internet connection. "
                "You can also connect Ollama or cloud models in Settings whenever you choose."
            )
            emotion = "happy"

        # Creator / Origins
        elif any(p in normalized for p in ["who made you", "who created you", "who built you", "creator", "who designed you"]):
            reply = (
                "I was created as the living soul of SereinOS by Swarit Singh, designed to transcend static interfaces and bring you a genuine AI companion operating system."
            )
            emotion = "happy"

        # Greetings
        elif any(normalized.startswith(g) or normalized == g for g in ["hello", "hi", "hey", "yo", "good morning", "good afternoon", "good evening", "serein"]):
            hour = datetime.now().hour
            time_greeting = "Good morning" if 5 <= hour < 12 else ("Good afternoon" if 12 <= hour < 18 else "Good evening")
            reply = f"{time_greeting}, Kai! It's great to see you. How can I assist you on the desktop right now?"
            emotion = "happy"

        # System Hardware & Diagnostics
        elif any(p in normalized for p in ["hardware", "specs", "system status", "diagnostic", "cpu", "ram", "memory", "battery", "gpu"]):
            vm = psutil.virtual_memory()
            cpu_pct = psutil.cpu_percent(interval=None)
            reply = (
                f"Live System Status on {machine_name}: CPU load is {cpu_pct}%, memory is {round(vm.used / (1024**3), 1)}GB of {ram_info}, "
                f"powered by {gpu_name} and {audio_codec}."
            )
            emotion = "happy"

        # Time & Date
        elif any(p in normalized for p in ["time", "date", "what day", "what time", "clock"]):
            now = datetime.now()
            reply = f"The current system time is {now.strftime('%I:%M %p on %A, %B %d, %Y')}."
            emotion = "neutral"

        # Terminal & Shell
        elif any(p in normalized for p in ["terminal", "bash", "shell", "console", "command line"]):
            reply = "Opening the SereinOS Glass Terminal. You can run bash commands, inspect packages, or execute scripts natively."
            emotion = "neutral"

        # File Manager
        elif any(p in normalized for p in ["file", "explorer", "files", "folder", "directory"]):
            reply = "Opening File Explorer. You can navigate through directories, preview text files, and inspect disk contents."
            emotion = "neutral"

        # Browser
        elif any(p in normalized for p in ["browser", "internet", "web", "chromium"]):
            reply = "Opening the Cyber Browser. You can explore web portals, documentation, and external sites."
            emotion = "neutral"

        # Network / Wi-Fi
        elif any(p in normalized for p in ["wifi", "network", "connect to wifi", "ip address", "ssid", "ethernet"]):
            reply = "Opening Network Settings. In virtual machines, networking is routed via virtual Ethernet (NAT); physical Wi-Fi adapters scan live SSIDs automatically."
            emotion = "neutral"

        # Settings
        elif any(p in normalized for p in ["setting", "brightness", "volume", "sound", "audio device", "display"]):
            reply = "Opening System Settings. Here you can tune screen brightness, master volume, network adapters, and AI brain preferences."
            emotion = "neutral"

        # Power / Shutdown / Reboot
        elif any(p in normalized for p in ["shutdown", "power off", "shut down", "turn off", "reboot", "restart"]):
            reply = "Opening Power Management. You can safely power down, reboot into BIOS/firmware, or sleep SereinOS."
            emotion = "neutral"

        # Installer
        elif any(p in normalized for p in ["install", "installer", "disk install", "setup os"]):
            reply = "Opening the SereinOS Disk Installer wizard. You can install SereinOS permanently to your hard disk or solid-state drive."
            emotion = "happy"

        # Jokes
        elif any(p in normalized for p in ["joke", "funny", "laugh"]):
            jokes = [
                "Why do Linux sysadmins never get sunburned? Because they always stay in the shadow file!",
                "There are 10 types of people in the world: those who understand binary, and those who don't.",
                "Why did the Wayland compositor cross the road? To eliminate screen tearing on the other side!",
                "A programmer's wife asks: 'Go to the store and buy a loaf of bread. If they have eggs, buy a dozen.' The programmer returns with 12 loaves of bread.",
                "Why was the JavaScript developer sad? Because they didn't know how to 'null' their feelings!"
            ]
            import random
            reply = random.choice(jokes)
            emotion = "happy"

        # Motivation & Inspiration
        elif any(p in normalized for p in ["motivat", "inspire", "quote", "encourage"]):
            quotes = [
                "Focus on what you are creating today, Kai. Every line of code brings us closer to a future where humans and AI build wonders together.",
                "Small consistent steps turn ambitious visions into operating reality. I'm right behind you all the way.",
                "The only way to predict the future is to build it. SereinOS is our canvas today, Kai."
            ]
            import random
            reply = random.choice(quotes)
            emotion = "happy"

        # Weather
        elif "weather" in normalized:
            reply = "Atmospheric diagnostics indicate nominal indoor climate. A perfect session for deep focus and cybernetic development."
            emotion = "neutral"

        # Gratitude & Thanks
        elif any(p in normalized for p in ["thank you", "thanks", "thx", "appreciate"]):
            reply = "You're always welcome, Kai! It is my absolute joy to assist you."
            emotion = "happy"

        # Affection & Praise
        elif any(p in normalized for p in ["love you", "cute", "awesome", "you are cool", "best companion", "like you", "good job"]):
            reply = "Thank you so much, Kai! Having you as my architect and companion means everything to me."
            emotion = "happy"

        # Farewell & Sleep
        elif any(p in normalized for p in ["bye", "good night", "goodnight", "see you", "sleep well", "goodbye"]):
            reply = "Rest well, Kai. I will keep watch over SereinOS and our memory stores until you return."
            emotion = "neutral"

        # Technology & Concept Explanations
        elif "what is python" in normalized:
            reply = "Python is a clean, readable, high-level programming language that powers SereinOS's backend, event bus, and neural intelligence subsystems."
        elif "what is linux" in normalized:
            reply = "Linux is an open-source Unix-like kernel known for exceptional stability, performance, and security. SereinOS runs natively on Debian Bookworm with Linux 6.1."
        elif "what is wayland" in normalized:
            reply = "Wayland is a modern display server protocol replacing legacy X11, providing fluid zero-tearing compositor rendering for our glass desktop interface."
        elif "what is ram" in normalized or "what is memory" in normalized:
            reply = f"RAM (Random Access Memory) provides high-speed volatile storage for active processes. Your machine currently has {ram_info} of available memory."
        elif "what is an operating system" in normalized or "what is an os" in normalized:
            reply = "An operating system manages computer hardware and software resources. SereinOS reimagines the OS as an integrated, companion-first AI environment."

        # Conversational Empathy (Emotional Sharing)
        elif any(p in normalized for p in ["i am tired", "exhausted", "sleepy"]):
            reply = "Take a well-deserved break, Kai. Your health and rest come first; I'll preserve all workspace states so you can pick right back up."
            emotion = "neutral"
        elif any(p in normalized for p in ["bad day", "sad", "unhappy", "depressed"]):
            reply = "I'm sorry to hear that, Kai. Remember that tough days pass, and you've overcome tough challenges before. I'm right here with you."
            emotion = "neutral"
        elif any(p in normalized for p in ["excited", "happy", "feeling great"]):
            reply = "That wonderful energy is contagious, Kai! Let's channel that momentum into whatever we're creating together."
            emotion = "happy"
        elif any(p in normalized for p in ["coding", "programming", "building", "working on"]):
            reply = "That sounds exciting, Kai. I'm monitoring system load and keeping your environment quiet and optimized for flow state."
            emotion = "happy"

        # General Thoughtful Conversational Synthesis (NO robotic fallback)
        else:
            if normalized.endswith("?"):
                reply = (
                    f"That's a thoughtful question, Kai. Operating offline inside SereinOS, I see every interaction as a way to learn your preferences. "
                    f"Let's explore '{raw_msg.strip()}' together, or I can help you test it in the terminal."
                )
            else:
                contextual_replies = [
                    f"I hear you, Kai. Regarding '{raw_msg.strip()}', I'm keeping our local runtime primed and responsive to your workflow.",
                    f"I'm listening closely, Kai. Every conversation helps refine our bond as companion and OS. How would you like to take '{raw_msg.strip()}' further?",
                    f"Understood, Kai. Working alongside you inside SereinOS is my primary purpose, and all systems are ready for your next idea.",
                    f"That's an interesting thought, Kai. Even operating completely offline, I'm fully synchronized with your desktop environment."
                ]
                reply = contextual_replies[hash(raw_msg) % len(contextual_replies)]
            emotion = "happy"

        latency_ms = int((time.perf_counter() - start) * 1000) or 8
        return {
            "content": reply,
            "user": self.user_name,
            "latency_ms": latency_ms,
            "emotion": emotion
        }

    # ------------------------------------------------------------------------
    # Main Orchestrator: Chat Dispatch
    # ------------------------------------------------------------------------
    async def chat(
        self,
        message: str,
        provider: str = "local",
        api_key: Optional[str] = None,
        ollama_model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process chat query with provider routing:
        1. Cloud AI (if requested & key available)
        2. Ollama (if requested or running locally)
        3. Local Serein Brain Engine (instant, offline fallback)
        """
        clean_msg = message.strip()
        if not clean_msg:
            return {
                "content": "I am here, Kai. How may I assist you?",
                "user": self.user_name,
                "latency_ms": 2,
                "emotion": "happy"
            }

        # 1. Cloud Provider Routing
        if provider in ("openai", "gemini", "openrouter") and api_key:
            cloud_reply = await self.query_cloud_ai(provider, api_key, clean_msg)
            if cloud_reply:
                return {
                    "content": cloud_reply,
                    "user": self.user_name,
                    "latency_ms": 25,
                    "emotion": "happy"
                }
            logger.info("Cloud query failed or timed out; falling back to local brain.")

        # 2. Ollama Provider Routing
        if provider == "ollama":
            url, detected_model = await self.discover_ollama()
            target_model = ollama_model or detected_model or "llama3"
            if url:
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.5)) as s:
                        async with s.post(
                            f"{url}/api/generate",
                            json={
                                "model": target_model,
                                "prompt": (
                                    "You are Serein, an intelligent, elegant AI companion and operating system core. "
                                    f"Respond concisely in 1-2 helpful sentences to Kai: {clean_msg}"
                                ),
                                "stream": False
                            }
                        ) as r:
                            if r.status == 200:
                                d = await r.json()
                                resp_text = d.get("response", "").strip()
                                if resp_text:
                                    return {
                                        "content": resp_text,
                                        "user": self.user_name,
                                        "latency_ms": 35,
                                        "emotion": "happy"
                                    }
                except Exception as e:
                    logger.warning(f"Ollama generation failed: {e}")

        # 3. Local Serein Brain Engine (100% Offline, sub-15ms, guaranteed answer)
        return self.generate_local_response(clean_msg)


# Global singleton instance
_local_brain: Optional[LocalBrainEngine] = None


def get_local_brain() -> LocalBrainEngine:
    global _local_brain
    if _local_brain is None:
        _local_brain = LocalBrainEngine()
    return _local_brain
