import json

from src.ai.providers.nvidia_provider import NvidiaProvider


class MemoryExtractor:

    def __init__(self):

        self.ai = NvidiaProvider(
            model="meta/llama-3.1-8b-instruct"
        )

    def extract(self, message):

        prompt = f"""
Return ONLY valid JSON.

User Message:
{message}

Format:

{{
    "remember": true,
    "content": "...",
    "importance": 0,
    "tags": [],
    "emotion": "neutral"
}}

OR

{{
    "remember": false
}}
"""

        response = self.ai.generate_response(
            prompt
        )

        try:
            return json.loads(response)

        except Exception:
            return {
                "remember": False
            }