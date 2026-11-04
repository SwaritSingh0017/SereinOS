from src.ai.providers.nvidia_provider import NvidiaProvider


class MemoryClassifier:

    def __init__(self):

        self.ai = NvidiaProvider()

    def classify(self, message):

        prompt = f"""
You are a memory classification system.

Determine whether the following message
contains information worth remembering
for a long-term AI companion.

Respond ONLY with:

YES

or

NO

Message:
{message}
"""

        result = self.ai.generate_response(
            prompt
        )

        return result.strip().upper()