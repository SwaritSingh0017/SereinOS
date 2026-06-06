import sqlite3
from datetime import datetime

DB_PATH = "data/database/serein.db"


class Database:

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()

    def initialize_serein(self):

        # Personality
        self.cursor.execute("SELECT COUNT(*) FROM personality_state")
        if self.cursor.fetchone()[0] == 0:

            self.cursor.execute("""
            INSERT INTO personality_state
            (
                calm,
                mature,
                cheerful,
                energetic,
                protective,
                companion,
                professional,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                90,
                85,
                70,
                60,
                80,
                85,
                90,
                datetime.now()
            ))

        # Emotion
        self.cursor.execute("SELECT COUNT(*) FROM emotion_state")
        if self.cursor.fetchone()[0] == 0:

            self.cursor.execute("""
            INSERT INTO emotion_state
            (
                happiness,
                curiosity,
                care,
                concern,
                pride,
                affection,
                loneliness,
                sadness,
                jealousy,
                possessiveness,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                50,
                70,
                60,
                20,
                10,
                0,
                0,
                0,
                0,
                0,
                datetime.now()
            ))

        # Relationship
        self.cursor.execute("SELECT COUNT(*) FROM relationship_state")
        if self.cursor.fetchone()[0] == 0:

            self.cursor.execute("""
            INSERT INTO relationship_state
            (
                trust,
                familiarity,
                attachment,
                interaction_count,
                total_conversation_minutes,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                0,
                0,
                0,
                0,
                0,
                datetime.now()
            ))

        # Growth
        self.cursor.execute("SELECT COUNT(*) FROM growth_state")
        if self.cursor.fetchone()[0] == 0:

            self.cursor.execute("""
            INSERT INTO growth_state
            (
                technical_interest,
                study_interest,
                productivity_interest,
                gaming_interest,
                social_interest,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                50,
                50,
                50,
                50,
                50,
                datetime.now()
            ))

        self.conn.commit()

        print("Serein initialized.")

    def create_identity(self, user_name):

        self.cursor.execute("""
        INSERT INTO identity
        (
            user_name,
            preferred_name,
            created_at,
            last_seen
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            user_name,
            None,
            datetime.now().isoformat(),
            datetime.now().isoformat()
        ))

        self.conn.commit()


    def get_identity(self):

        self.cursor.execute("""
        SELECT *
        FROM identity
        LIMIT 1
        """)

        return self.cursor.fetchone()


    def update_last_seen(self):

        self.cursor.execute("""
        UPDATE identity
        SET last_seen = ?
        WHERE id = 1
        """,
        (
            datetime.now().isoformat(),
        ))

        self.conn.commit()


    def get_display_name(self):

        self.cursor.execute("""
        SELECT user_name, preferred_name
        FROM identity
        LIMIT 1
        """)

        row = self.cursor.fetchone()

        if not row:
            return None

        user_name, preferred_name = row

        return preferred_name if preferred_name else user_name


    def save_memory(
            self,
            content,
            tags,
            importance,
            emotion,
            source
    ):

        self.cursor.execute("""
        INSERT INTO memories
        (
            content,
            tags,
            importance,
            emotion,
            source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            content,
            tags,
            importance,
            emotion,
            source,
            datetime.now().isoformat()
        ))

        self.conn.commit()


    def close(self):
        self.conn.close()


if __name__ == "__main__":

    db = Database()

    db.initialize_serein()

    db.close()