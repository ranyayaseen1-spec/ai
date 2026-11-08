
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS episodes (
        episode_id INTEGER PRIMARY KEY,
        season_id INTEGER,
        episode_number INTEGER,
        chat_id INTEGER,
        FOREIGN KEY (chat_id) REFERENCES conversations(chat_id)
    )

    """)

  
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episode_versions (
        version_id INTEGER PRIMARY KEY,
        version_number TEXT,
        episode_id INTEGER NOT NULL,
        transcript_text TEXT,
        is_canon INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (episode_id) REFERENCES episodes(episode_id),
        UNIQUE(episode_id, version_number)
    )
""")

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS one_canon_per_episode
    ON episode_versions(episode_id)
    WHERE is_canon = 1
""")

    # Haal alle tabelnamen op
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()

    # Laat de tabellen zien in de terminal
    print(tables)

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

def save_episode(season_id, episode_number, chat_id):
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    cursor = connection.cursor()

    cursor.execute(
        "INSERT INTO episodes (season_id, episode_number, chat_id) VALUES (?, ?, ?)",
        (season_id, episode_number, chat_id)
    )

    connection.commit()
    connection.close()



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

#Slaat seizoen en versie op
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

def delete_memory(memory_id):
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM memories WHERE memory_id = ?",
    (memory_id,))
    connection.commit()
    connection.close()


if __name__ == "__main__":
    initialize_database()

    # Verwijder alleen herinnering 2
    #delete_memory(2)

    # Open de database
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    # Haal de overgebleven herinneringen op
    cursor.execute("SELECT * FROM memories")
    memories = cursor.fetchall()

    # Bekijk welke tabellen bestaan
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()

    # Print de resultaten
    print(tables)
    print(memories)
    print("Database is klaar!")
    print(get_messages(1))
    print(get_memories(1))

    # Sluit de verbinding
    connection.close()
    initialize_database()

