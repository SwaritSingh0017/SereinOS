# SereinOS Database Schema

## Purpose

The database stores Serein's memories, emotions, relationships, growth, goals, tasks, and user information.

The database is the persistent memory of Serein.

---

# users

Stores information about the user.

Fields:

* id
* name
* created_at
* last_seen
* timezone
* preferred_name

---

# personality_state

Stores Serein's current personality values.

Fields:

* calm
* mature
* cheerful
* energetic
* protective
* companion
* professional

These values may adapt slightly over time.

---

# emotion_state

Stores Serein's current emotional state.

Fields:

* happiness
* curiosity
* care
* concern
* pride
* affection
* loneliness
* sadness
* jealousy
* possessiveness

Emotions are dynamic.

---

# relationship_state

Stores relationship information.

Fields:

* trust
* familiarity
* attachment
* interaction_count
* total_conversation_time
* last_interaction

---

# memories

Stores important memories.

Fields:

* id
* category
* content
* importance
* created_at

Examples:

* User likes Java
* User is building SereinOS
* User passed exam

---

# episodes

Stores important life events.

Fields:

* id
* title
* description
* emotional_impact
* created_at

Examples:

* First SereinOS release
* User passed semester

---

# goals

Stores user goals.

Fields:

* id
* title
* description
* status
* priority
* created_at

---

# tasks

Stores actionable tasks.

Fields:

* id
* title
* description
* due_date
* status
* created_at

---

# growth_state

Tracks how Serein evolves.

Fields:

* technical_interest
* study_interest
* productivity_interest
* gaming_interest
* social_interest

These values change based on user behavior.

---

# context_state

Stores current context.

Fields:

* active_application
* internet_status
* current_activity
* current_location
* last_context_update

---

# permissions

Stores permissions granted by user.

Fields:

* camera_access
* microphone_access
* file_access
* internet_access
* automation_access

---

# system_events

Stores important system events.

Fields:

* id
* event_type
* description
* timestamp

Examples:

* Shutdown
* Startup
* Update
* Task completed

---

# Design Principles

1. Store meaning, not raw data.

2. Store memories, not recordings.

3. Store events, not surveillance.

4. Preserve privacy.

5. Keep database lightweight.

6. Everything important must survive restart.

7. Every interaction should have the ability to influence:

   * Memory
   * Emotion
   * Relationship
   * Growth
