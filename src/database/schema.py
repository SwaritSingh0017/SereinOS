import sqlite3


DB_PATH = "data/database/serein.db"


def create_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Identity
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS identity (
        id INTEGER PRIMARY KEY,
        user_name TEXT,
        preferred_name TEXT,
        timezone TEXT,
        created_at DATETIME,
        last_seen DATETIME
    )
    """)

    # Personality
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS personality_state (
        id INTEGER PRIMARY KEY,
        calm INTEGER,
        mature INTEGER,
        cheerful INTEGER,
        energetic INTEGER,
        protective INTEGER,
        companion INTEGER,
        professional INTEGER,
        updated_at DATETIME
    )
    """)

    # Emotions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS emotion_state (
        id INTEGER PRIMARY KEY,
        happiness INTEGER,
        curiosity INTEGER,
        care INTEGER,
        concern INTEGER,
        pride INTEGER,
        affection INTEGER,
        loneliness INTEGER,
        sadness INTEGER,
        jealousy INTEGER,
        possessiveness INTEGER,
        updated_at DATETIME
    )
    """)

    # Relationship
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS relationship_state (
        id INTEGER PRIMARY KEY,
        trust INTEGER,
        familiarity INTEGER,
        attachment INTEGER,
        interaction_count INTEGER,
        total_conversation_minutes INTEGER,
        updated_at DATETIME
    )
    """)

    # Growth
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS growth_state (
        id INTEGER PRIMARY KEY,
        technical_interest INTEGER,
        study_interest INTEGER,
        productivity_interest INTEGER,
        gaming_interest INTEGER,
        social_interest INTEGER,
        updated_at DATETIME
    )
    """)

    # Memories
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY,
        content TEXT,
        tags TEXT,
        importance INTEGER,
        emotion TEXT,
        source TEXT,
        created_at DATETIME
    )
    """)

    # Episodes
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY,
        title TEXT,
        description TEXT,
        emotional_impact INTEGER,
        created_at DATETIME
    )
    """)

    # Goals
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY,
        title TEXT,
        description TEXT,
        priority INTEGER,
        status TEXT,
        created_at DATETIME,
        completed_at DATETIME
    )
    """)

    # Tasks
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        title TEXT,
        description TEXT,
        due_date DATETIME,
        status TEXT,
        created_at DATETIME,
        completed_at DATETIME
    )
    """)

    # Permissions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY,
        camera_access BOOLEAN,
        microphone_access BOOLEAN,
        file_access BOOLEAN,
        internet_access BOOLEAN,
        automation_access BOOLEAN
    )
    """)

    # System Events
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY,
        event_type TEXT,
        description TEXT,
        timestamp DATETIME
    )
    """)

    conn.commit()
    conn.close()

    print("Serein database initialized.")


if __name__ == "__main__":
    create_database()