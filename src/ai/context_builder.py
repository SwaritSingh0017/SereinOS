from pathlib import Path
from src.database.database import Database


class ContextBuilder:

    def __init__(self):

        self.db = Database()

        prompt_path = Path(
            "docs/SEREIN_CORE_PROMPT.md"
        )

        self.core_prompt = prompt_path.read_text(
            encoding="utf-8"
        )

    def build_context(self, user_message):

        identity = self.db.get_identity()

        user_name = "User"

        if identity:
            user_name = identity[1]

        memories = self.db.get_all_memories()

        memory_text = ""

        for memory in memories[:10]:

            memory_text += f"- {memory[0]}\n"

        context = f"""
{self.core_prompt}

---------------------------------

USER NAME:
{user_name}

---------------------------------

KNOWN MEMORIES:

{memory_text}

---------------------------------

CURRENT MESSAGE:

{user_message}
"""

        return context

    def close(self):

        self.db.close()