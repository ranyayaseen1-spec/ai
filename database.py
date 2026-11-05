
import sqlite3
from pathlib import Path

# Zorg dat de database altijd naast database.py staat.
DB_PATH = Path(__file__).resolve().parent / "chatmemory.db"

#Crieert Tabel conversations
def initialize_database():
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            created_at DATETIME,
            is_pinned INTEGER NOT NULL DEFAULT 0
        )
    """)

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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS memories (
        memory_id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        season INTEGER,
        episode INTEGER,
        Version TEXT,
        FOREIGN KEY (chat_id) REFERENCES conversations(chat_id)
    )
    """)

    connection.commit()
    connection.close()

#maakt nieuw gesprek aan
def create_conversation(title):
        connection = sqlite3.connect(DB_PATH)
        cursor = connection.cursor()
        sql = ''' INSERT INTO conversations(title)
                    
              VALUES(?) '''
        cursor.execute(
          
    sql,
    (title,))
        new_chat_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return new_chat_id

def get_conversations():
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()
    cursor.execute("select * from conversations")
    conversations = cursor.fetchall()
    connection.close()
    return conversations
    

#slaat gesprekken op
def save_message(chat_id, sender, content, created_at):
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO messages (chat_id, sender, content, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, sender, content, created_at)
    )

    connection.commit()
    connection.close()

#pakt de gesprekken in de database
def get_messages(chat_id):
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY message_id",
        (chat_id,)
    )

    messages = cursor.fetchall()
    connection.close()

    return messages

def save_memory(chat_id, content, season, episode, version):
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    cursor = connection.cursor()
    cursor.execute(
            "INSERT INTO memories (chat_id, season, content, episode, version) VALUES (?, ?, ?, ?, ?)",
            (chat_id, season, content, episode, version)
        )
    
    connection.commit()
    connection.close()

def get_memories(chat_id):
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM memories WHERE chat_id = ?",
        (chat_id,)
    )

    memories = cursor.fetchall()
    connection.close()
    return memories             

if __name__ == "__main__":
    initialize_database()
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM memories")
    memories = cursor.fetchall()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    #save_memory(1, "Emma ontdekt een geheime doorgang", 1, 1, "1.0")
    print(tables)
    print(memories)
    print("Database is klaar!")
    print(get_messages(1))
    print(get_memories(1))
    connection.close()
