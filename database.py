import sqlite3
connection = sqlite3.connect("chatmemory.db")
cursor = connection.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        chat_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        created_at DATETIME,
        is_pinned INTEGER NOT NULL DEFAULT 0
    )
""")

# cursor.execute(
#     "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
#     ("Hogwarts AU", "2026-11-05 17:00")
# )

cursor.execute("SELECT * FROM conversations")
print(cursor.fetchall())
connection.commit()

connection.close()
