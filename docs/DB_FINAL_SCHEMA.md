# SereinOS Final Database Schema

## Database

SQLite

Database File:

serein.db

Architecture:

Single User
Single Serein

---

# identity

Stores permanent user identity.

Columns:

* id INTEGER PRIMARY KEY
* user_name TEXT
* preferred_name TEXT
* timezone TEXT
* created_at DATETIME
* last_seen DATETIME

---

# personality_state

Stores current personality values.

Columns:

* id INTEGER PRIMARY KEY

* calm INTEGER

* mature INTEGER

* cheerful INTEGER

* energetic INTEGER

* protective INTEGER

* companion INTEGER

* professional INTEGER

* updated_at DATETIME

Range:

0-100

---

# emotion_state

Stores current emotional state.

Columns:

* id INTEGER PRIMARY KEY

* happiness INTEGER

* curiosity INTEGER

* care INTEGER

* concern INTEGER

* pride INTEGER

* affection INTEGER

* loneliness INTEGER

* sadness INTEGER

* jealousy INTEGER

* possessiveness INTEGER

* updated_at DATETIME

Range:

0-100

---

# relationship_state

Stores relationship values.

Columns:

* id INTEGER PRIMARY KEY

* trust INTEGER

* familiarity INTEGER

* attachment INTEGER

* interaction_count INTEGER

* total_conversation_minutes INTEGER

* updated_at DATETIME

Range:

0-100

---

# growth_state

Stores long-term growth.

Columns:

* id INTEGER PRIMARY KEY

* technical_interest INTEGER

* study_interest INTEGER

* productivity_interest INTEGER

* gaming_interest INTEGER

* social_interest INTEGER

* updated_at DATETIME

Range:

0-100

---

# memories

Stores long-term memories.

Columns:

* id INTEGER PRIMARY KEY

* content TEXT

* tags TEXT

* importance INTEGER

* emotion TEXT

* source TEXT

* created_at DATETIME

Examples:

source:

voice
text
vision
system

---

# episodes

Stores major life events.

Columns:

* id INTEGER PRIMARY KEY

* title TEXT

* description TEXT

* emotional_impact INTEGER

* created_at DATETIME

---

# goals

Stores user goals.

Columns:

* id INTEGER PRIMARY KEY

* title TEXT

* description TEXT

* priority INTEGER

* status TEXT

* created_at DATETIME

* completed_at DATETIME

---

# tasks

Stores actionable tasks.

Columns:

* id INTEGER PRIMARY KEY

* title TEXT

* description TEXT

* due_date DATETIME

* status TEXT

* created_at DATETIME

* completed_at DATETIME

---

# permissions

Stores granted permissions.

Columns:

* id INTEGER PRIMARY KEY

* camera_access BOOLEAN

* microphone_access BOOLEAN

* file_access BOOLEAN

* internet_access BOOLEAN

* automation_access BOOLEAN

---

# system_events

Stores important events.

Columns:

* id INTEGER PRIMARY KEY

* event_type TEXT

* description TEXT

* timestamp DATETIME

Examples:

startup
shutdown
memory_created
goal_completed

---

# Design Rules

1. Single User

2. Local First

3. Privacy First

4. Memory Is Meaningful

5. Growth Comes From Experience

6. Relationship Comes From Interaction

7. Everything Important Survives Restart
