
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
    conversations = cursor.fetchall()

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


if __name__ == "__main__":
    initialize_database()
    print("Database is klaar!")
    print(get_messages(1))
