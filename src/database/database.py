import sqlite3
import json
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

        # Permissions
        self.cursor.execute("SELECT COUNT(*) FROM permissions")
        if self.cursor.fetchone()[0] == 0:

            self.cursor.execute("""
            INSERT INTO permissions
            (
                camera_access,
                microphone_access,
                file_access,
                internet_access,
                automation_access
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                0, 0, 1, 1, 0
            ))

        self.conn.commit()

        print("Serein initialized.")

    def create_identity(self, user_name):

        self.cursor.execute("""
        INSERT INTO identity
        (
            user_name,
            preferred_name,
            timezone,
            created_at,
            last_seen
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_name,
            None,
            "UTC",
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

    
    def update_identity(self, user_name=None, preferred_name=None, timezone=None, last_seen=None):
        updates = []
        params = []
        
        if user_name is not None:
            updates.append("user_name = ?")
            params.append(user_name)
        if preferred_name is not None:
            updates.append("preferred_name = ?")
            params.append(preferred_name)
        if timezone is not None:
            updates.append("timezone = ?")
            params.append(timezone)
        if last_seen is not None:
            updates.append("last_seen = ?")
            params.append(last_seen.isoformat() if isinstance(last_seen, datetime) else last_seen)
        
        if updates:
            self.cursor.execute(f"""
            UPDATE identity
            SET {", ".join(updates)}
            WHERE id = 1
            """, params)
            self.conn.commit()

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
        return self.cursor.lastrowid
    
    def get_all_memories(self):
        self.cursor.execute("""
        SELECT id, content, tags, importance, emotion, source, created_at, memory_type
        FROM memories
        ORDER BY importance DESC
        """)
        return self.cursor.fetchall()

    def search_memories(self, keyword):

        self.cursor.execute("""
    SELECT id, content, tags, importance, emotion, source, created_at, memory_type
    FROM memories
    WHERE content LIKE ?
       OR tags LIKE ?
    """,
    (
        f"%{keyword}%",
        f"%{keyword}%"
    ))

        return self.cursor.fetchall()

    def update_memory_importance(self, memory_id: int, importance: int):
        self.cursor.execute("""
        UPDATE memories SET importance = ? WHERE id = ?
        """, (importance, memory_id))
        self.conn.commit()

    def update_memory_type(self, memory_id: int, memory_type: str):
        self.cursor.execute("""
        UPDATE memories SET memory_type = ? WHERE id = ?
        """, (memory_type, memory_id))
        self.conn.commit()

    def delete_memory(self, memory_id: int):
        self.cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
    
    # Personality methods
    def get_personality(self):
        self.cursor.execute("SELECT * FROM personality_state LIMIT 1")
        return self.cursor.fetchone()
    
    def update_personality(self, calm, mature, cheerful, energetic, protective, companion, professional):
        self.cursor.execute("""
        UPDATE personality_state 
        SET calm=?, mature=?, cheerful=?, energetic=?, protective=?, companion=?, professional=?, updated_at=?
        WHERE id = 1
        """, (calm, mature, cheerful, energetic, protective, companion, professional, datetime.now()))
        self.conn.commit()
    
    # Emotion methods
    def get_emotions(self):
        self.cursor.execute("SELECT * FROM emotion_state LIMIT 1")
        return self.cursor.fetchone()
    
    def update_emotions(self, happiness, curiosity, care, concern, pride, excitement, 
                       affection, loneliness, sadness, jealousy, possessiveness):
        self.cursor.execute("""
        UPDATE emotion_state 
        SET happiness=?, curiosity=?, care=?, concern=?, pride=?, excitement=?,
            affection=?, loneliness=?, sadness=?, jealousy=?, possessiveness=?, updated_at=?
        WHERE id = 1
        """, (happiness, curiosity, care, concern, pride, excitement,
              affection, loneliness, sadness, jealousy, possessiveness, datetime.now()))
        self.conn.commit()
    
    # Relationship methods
    def get_relationship(self):
        self.cursor.execute("SELECT * FROM relationship_state LIMIT 1")
        return self.cursor.fetchone()
    
    def update_relationship(self, trust, familiarity, attachment, interaction_count, total_conversation_minutes):
        self.cursor.execute("""
        UPDATE relationship_state 
        SET trust=?, familiarity=?, attachment=?, interaction_count=?, total_conversation_minutes=?, updated_at=?
        WHERE id = 1
        """, (trust, familiarity, attachment, interaction_count, total_conversation_minutes, datetime.now()))
        self.conn.commit()
    
    # Growth methods
    def get_growth(self):
        self.cursor.execute("SELECT * FROM growth_state LIMIT 1")
        return self.cursor.fetchone()
    
    def update_growth(self, technical_interest, study_interest, productivity_interest, gaming_interest, social_interest):
        self.cursor.execute("""
        UPDATE growth_state 
        SET technical_interest=?, study_interest=?, productivity_interest=?, gaming_interest=?, social_interest=?, updated_at=?
        WHERE id = 1
        """, (technical_interest, study_interest, productivity_interest, gaming_interest, social_interest, datetime.now()))
        self.conn.commit()
    
    # Permission methods
    def get_permissions(self):
        self.cursor.execute("SELECT * FROM permissions LIMIT 1")
        return self.cursor.fetchone()
    
    def update_permissions(self, camera_access, microphone_access, file_access, internet_access, automation_access):
        self.cursor.execute("""
        UPDATE permissions 
        SET camera_access=?, microphone_access=?, file_access=?, internet_access=?, automation_access=?
        WHERE id = 1
        """, (camera_access, microphone_access, file_access, internet_access, automation_access))
        self.conn.commit()
    
    # System events
    def add_system_event(self, event_type: str, description: str):
        self.cursor.execute("""
        INSERT INTO system_events (event_type, description, timestamp)
        VALUES (?, ?, ?)
        """, (event_type, description, datetime.now().isoformat()))
        self.conn.commit()
    
    def get_system_events(self, event_type: str = None, limit: int = 100):
        if event_type:
            self.cursor.execute("""
            SELECT * FROM system_events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?
            """, (event_type, limit))
        else:
            self.cursor.execute("""
            SELECT * FROM system_events ORDER BY timestamp DESC LIMIT ?
            """, (limit,))
        return self.cursor.fetchall()
    
    # Tasks
    def add_task(self, title: str, description: str = "", due_date: str = None):
        self.cursor.execute("""
        INSERT INTO tasks (title, description, due_date, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (title, description, due_date, "pending", datetime.now().isoformat()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def complete_task(self, task_id: int):
        self.cursor.execute("""
        UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?
        """, ("completed", datetime.now().isoformat(), task_id))
        self.conn.commit()
    
    def get_tasks(self, status: str = None):
        if status:
            self.cursor.execute("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            self.cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        return self.cursor.fetchall()
    
    def close(self):
        self.conn.close()


if __name__ == "__main__":

    db = Database()

    db.initialize_serein()

    db.close()