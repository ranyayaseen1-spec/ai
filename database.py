import sqlite3
connection = sqlite3.connect("chatmemory.db")
connection.execute("PRAGMA foreign_keys = ON") #controleert gesprekken
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

cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY (chat_id) REFERENCES conversations(chat_id)


    )
""")

# cursor.execute(
#     "INSERT INTO messages (chat_id, sender, content, created_at) VALUES (?, ?, ?, ?)",
#     (1, "user", "My Hogwarts OC is named Emma!", "2026-11-05 17:20")
# )
connection.commit()
cursor.execute(
    "SELECT * FROM messages WHERE chat_id = ?",
    (1,)
)
print(cursor.fetchall())
connection.close()
