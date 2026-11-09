import sqlite3
from pathlib import Path

# De database staat naast dit Python-bestand.
DB_PATH = Path(__file__).resolve().parent / "chatmemory.db"


# ==========================================
# DATABASE INITIALISEREN
# ==========================================

def initialize_database():
    with sqlite3.connect(DB_PATH) as connection:
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
                version TEXT,
                FOREIGN KEY (chat_id) REFERENCES conversations(chat_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id INTEGER PRIMARY KEY,
                season_id INTEGER,
                episode_number INTEGER,
                chat_id INTEGER,
                FOREIGN KEY (chat_id) REFERENCES conversations(chat_id),
                UNIQUE(chat_id, season_id, episode_number)
            )
        """)

        # Bescherm ook databases waarin de episodes-tabel al eerder bestond.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS unique_episode
            ON episodes(chat_id, season_id, episode_number)
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

        # Per aflevering kan maximaal één versie canon zijn.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS one_canon_per_episode
            ON episode_versions(episode_id)
            WHERE is_canon = 1
        """)


# ==========================================
# GESPREKKEN
# ==========================================

def create_conversation(title):
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "INSERT INTO conversations (title) VALUES (?)",
            (title,)
        )
        return cursor.lastrowid


def get_conversations():
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            "SELECT * FROM conversations ORDER BY chat_id"
        ).fetchall()


# ==========================================
# AFLEVERINGEN
# ==========================================

def save_episode(season_id, episode_number, chat_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT INTO episodes (season_id, episode_number, chat_id)
            VALUES (?, ?, ?)
            """,
            (season_id, episode_number, chat_id)
        )
        return cursor.lastrowid


def get_episodes(chat_id):
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            """
            SELECT * FROM episodes
            WHERE chat_id = ?
            ORDER BY season_id, episode_number
            """,
            (chat_id,)
        ).fetchall()


# ==========================================
# AFLEVERINGSVERSIES
# ==========================================

def save_episode_version(episode_id, version_number, transcript_text, is_canon=0):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT INTO episode_versions (
                episode_id, version_number, transcript_text, is_canon
            )
            VALUES (?, ?, ?, ?)
            """,
            (episode_id, version_number, transcript_text, is_canon)
        )
        return cursor.lastrowid


def get_episode_versions(episode_id):
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            """
            SELECT * FROM episode_versions
            WHERE episode_id = ?
            ORDER BY version_id
            """,
            (episode_id,)
        ).fetchall()


# ==========================================
# CHATBERICHTEN
# ==========================================

def save_message(chat_id, sender, content, created_at):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO messages (chat_id, sender, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, sender, content, created_at)
        )


def get_messages(chat_id):
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            """
            SELECT * FROM messages
            WHERE chat_id = ?
            ORDER BY message_id
            """,
            (chat_id,)
        ).fetchall()


# ==========================================
# HERINNERINGEN
# ==========================================

def save_memory(chat_id, content, season, episode, version):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO memories (chat_id, season, content, episode, version)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, season, content, episode, version)
        )


def get_memories(chat_id):
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            "SELECT * FROM memories WHERE chat_id = ?",
            (chat_id,)
        ).fetchall()


def delete_memory(memory_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "DELETE FROM memories WHERE memory_id = ?",
            (memory_id,)
        )

def set_canon_version(episode_id, version_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")

        # Controleer of deze versie bij de aflevering hoort.
        version = connection.execute(
            """
            SELECT version_id
            FROM episode_versions
            WHERE episode_id = ? AND version_id = ?
            """,
            (episode_id, version_id)
        ).fetchone()

        if version is None:
            raise ValueError(
                "Deze versie bestaat niet voor deze aflevering."
            )

        # Haal de canonstatus van alle versies weg.
        connection.execute(
            """
            UPDATE episode_versions
            SET is_canon = 0
            WHERE episode_id = ?
            """,
            (episode_id,)
        )

        # Maak de gekozen versie canon.
        connection.execute(
            """
            UPDATE episode_versions
            SET is_canon = 1
            WHERE episode_id = ? AND version_id = ?
            """,
            (episode_id, version_id)
        )

# ==========================================
# TESTEN (zonder dubbele testgegevens)
# ==========================================

# ==========================================
# TESTEN
# ==========================================

if __name__ == "__main__":
    initialize_database()

    print("Astra's database is klaar!")

    print("\nGesprekken:")
    print(get_conversations())

    print("\nBerichten van chat 1:")
    print(get_messages(1))

    print("\nHerinneringen van chat 1:")
    print(get_memories(1))

    print("\nAfleveringen van chat 1:")
    print(get_episodes(1))

    # Kijk of versie 1.1 al bestaat.
    versions = get_episode_versions(1)

    version_11 = next(
        (version for version in versions if version[1] == "1.1"),
        None
    )

    if version_11 is None:
        new_version_id = save_episode_version(
            episode_id=1,
            version_number="1.1",
            transcript_text=(
                "Emma ontdekt een geheime doorgang "
                "en hoort mysterieuze voetstappen."
            )
        )
        print("\nVersie 1.1 aangemaakt!")
    else:
        new_version_id = version_11[0]
        print("\nVersie 1.1 bestaat al!")

    # Maak versie 1.1 canon.
    set_canon_version(1, new_version_id)

    print("\nVersies na de canon-switch:")
    print(get_episode_versions(1))