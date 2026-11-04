import sqlite3
conn = sqlite3.connect('data/database/serein.db')
cursor = conn.cursor()
cursor.execute('SELECT sql FROM sqlite_master WHERE type="table"')
for table in cursor.fetchall():
    print(table[0])
    print("---")