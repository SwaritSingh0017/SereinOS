import sqlite3
conn = sqlite3.connect('data/database/serein.db')
cursor = conn.cursor()

# Add memory_type column if not exists
try:
    cursor.execute('ALTER TABLE memories ADD COLUMN memory_type TEXT DEFAULT "semantic"')
    print('Added memory_type column')
except Exception as e:
    print(f'memory_type: {e}')

# Add excitement column to emotion_state if not exists
try:
    cursor.execute('ALTER TABLE emotion_state ADD COLUMN excitement INTEGER DEFAULT 0')
    print('Added excitement column')
except Exception as e:
    print(f'excitement: {e}')

conn.commit()
conn.close()
print('Schema updates complete')