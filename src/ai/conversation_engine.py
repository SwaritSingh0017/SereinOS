from src.ai.context_builder import ContextBuilder
from src.ai.providers.nvidia_provider import NvidiaProvider


class ConversationEngine:

    def __init__(self):

        self.context_builder = ContextBuilder()

        self.provider = NvidiaProvider(
            model="meta/llama-3.3-70b-instruct"
        )

    def chat(self, user_message):

        prompt = self.context_builder.build_context(
            user_message
        )

        return self.provider.generate_response(
            prompt
        )

    def close(self):

        self.context_builder.close()