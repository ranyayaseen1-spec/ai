
"""
ASTRA AI - MISSIE 4 + 5
COMPLETE GEHEUGEN- EN CANONTEST

Start vanuit je projecthoofdmap:

    python ai/test_astra_geheugen.py

Deze test:
- Gebruikt een aparte tijdelijke database.
- Wijzigt jouw echte chatmemory.db NIET.
- Test oude berichten van 90 dagen geleden.
- Test AU-isolatie.
- Test canonversies en canonwissels.
- Test het bijwerken van oude berichten.
- Test de volledige chatfunctie zonder Ollama-aanroep.
- Controleert de semantische zoekindex.

Er wordt een nep-embeddingmodel gebruikt.
Dit test de werking van de code, niet de kwaliteit
van het echte AI-model.
"""

import hashlib
import sqlite3
import sys
import tempfile
import unittest

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np


# ==========================================
# IMPORTS
# ==========================================

AI_DIR = Path(__file__).resolve().parent

if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

import database
import semantic_memory
import astra_chat


# ==========================================
# NEP-EMBEDDINGMODEL
# ==========================================

class FakeEmbeddingModel:
    """
    Maakt reproduceerbare tekstvectoren.

    Geen download of internet nodig.
    """

    def encode(
        self,
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True
    ):
        if isinstance(texts, str):
            texts = [texts]

        vectors = []

        for text in texts:
            vector = np.zeros(
                256,
                dtype=np.float32
            )

            words = str(text).lower().split()

            for word in words:
                word = word.strip(
                    ".,!?;:()[]{}\"'"
                )

                if not word:
                    continue

                digest = hashlib.sha256(
                    word.encode("utf-8")
                ).digest()

                index = int.from_bytes(
                    digest[:4],
                    "little"
                ) % len(vector)

                vector[index] += 1

            norm = np.linalg.norm(vector)

            if normalize_embeddings and norm > 0:
                vector /= norm

            vectors.append(vector)

        return np.asarray(
            vectors,
            dtype=np.float32
        )


# ==========================================
# TESTDATABASE
# ==========================================

def create_test_schema(path):
    """
    Maak alleen de tabellen die nodig zijn.

    We bouwen de testdatabase rechtstreeks
    op om verschillen tussen oude versies
    van database.py te vermijden.
    """

    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "PRAGMA foreign_keys = ON"
        )

        with db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT,
                    is_pinned INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT,
                    FOREIGN KEY(chat_id)
                        REFERENCES conversations(chat_id)
                );

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    season INTEGER,
                    episode INTEGER,
                    version TEXT,
                    FOREIGN KEY(chat_id)
                        REFERENCES conversations(chat_id)
                );

                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id INTEGER PRIMARY KEY,
                    season_id INTEGER,
                    episode_number INTEGER,
                    chat_id INTEGER,
                    FOREIGN KEY(chat_id)
                        REFERENCES conversations(chat_id),
                    UNIQUE(
                        chat_id,
                        season_id,
                        episode_number
                    )
                );

                CREATE TABLE IF NOT EXISTS episode_versions (
                    version_id INTEGER PRIMARY KEY,
                    version_number TEXT,
                    episode_id INTEGER NOT NULL,
                    transcript_text TEXT,
                    is_canon INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(episode_id)
                        REFERENCES episodes(episode_id),
                    UNIQUE(
                        episode_id,
                        version_number
                    )
                );

                CREATE UNIQUE INDEX IF NOT EXISTS
                    one_canon_per_episode
                ON episode_versions(episode_id)
                WHERE is_canon = 1;
                """
            )


# ==========================================
# TESTKLASSE
# ==========================================

class AstraMemoryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """
        Eén tijdelijke database voor de tests.

        We gebruiken een gewone tijdelijke map
        zonder automatische verwijdering.
        Dit voorkomt WinError 32 wanneer oudere
        databasefuncties nog een verbinding
        vasthouden op Windows.
        """

        cls.temp_path = Path(
            tempfile.mkdtemp(
                prefix="astra_memory_test_"
            )
        )

        cls.test_db = (
            cls.temp_path / "astra_test.db"
        )

        cls.fake_model = FakeEmbeddingModel()

        cls.patches = [
            patch.object(
                database,
                "DB_PATH",
                cls.test_db
            ),
            patch.object(
                semantic_memory,
                "DB_PATH",
                cls.test_db
            ),
            patch.object(
                astra_chat,
                "DB_PATH",
                cls.test_db
            ),
            patch.object(
                semantic_memory,
                "get_model",
                return_value=cls.fake_model
            ),
            patch.object(
                astra_chat,
                "get_model",
                return_value=cls.fake_model
            ),
        ]

        for item in cls.patches:
            item.start()

        create_test_schema(
            cls.test_db
        )

        semantic_memory.initialize_semantic_memory()
        astra_chat.initialize_chat_search()

    @classmethod
    def tearDownClass(cls):
        for item in reversed(cls.patches):
            item.stop()

        # We verwijderen de tijdelijke map niet
        # tijdens het Python-proces. Windows kan
        # bestanden anders nog vergrendeld zien.
        #
        # De map bevat uitsluitend testgegevens.
        print(
            "\nTijdelijke testdatabase:",
            cls.test_db
        )

    def setUp(self):
        """
        Begin iedere test met dezelfde
        schone gegevens.

        Alle testgegevens blijven in de
        tijdelijke database.
        """

        with closing(
            sqlite3.connect(self.test_db)
        ) as db:

            db.execute(
                "PRAGMA foreign_keys = ON"
            )

            with db:
                db.execute(
                    "DELETE FROM chat_message_chunks"
                )

                db.execute(
                    "DELETE FROM episode_chunks"
                )

                db.execute(
                    "DELETE FROM memory_embeddings"
                )

                db.execute(
                    "DELETE FROM messages"
                )

                db.execute(
                    "DELETE FROM memories"
                )

                db.execute(
                    "DELETE FROM episode_versions"
                )

                db.execute(
                    "DELETE FROM episodes"
                )

                db.execute(
                    "DELETE FROM conversations"
                )

                db.execute(
                    """
                    INSERT INTO conversations
                    (chat_id, title)
                    VALUES
                    (1, 'Hogwarts AU'),
                    (2, 'Nico Chaos AU')
                    """
                )

                db.execute(
                    """
                    INSERT INTO episodes
                    (
                        episode_id,
                        season_id,
                        episode_number,
                        chat_id
                    )
                    VALUES (1, 1, 2, 1)
                    """
                )

                db.execute(
                    """
                    INSERT INTO episode_versions
                    (
                        version_id,
                        version_number,
                        episode_id,
                        transcript_text,
                        is_canon
                    )
                    VALUES
                    (
                        1,
                        '1.0',
                        1,
                        'Emma ging naar de bibliotheek.',
                        0
                    ),
                    (
                        2,
                        '1.1',
                        1,
                        'Emma viel van de trap. '
                        || 'Iedereen lachte behalve Nico.',
                        1
                    )
                    """
                )

                old_date = (
                    datetime.now(timezone.utc)
                    - timedelta(days=90)
                ).isoformat()

                db.execute(
                    """
                    INSERT INTO messages
                    (
                        message_id,
                        chat_id,
                        sender,
                        content,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        "user",
                        "Weet je nog dat Emma van de trap "
                        "viel en Nico haar hielp?",
                        old_date
                    )
                )

                db.execute(
                    """
                    INSERT INTO messages
                    (
                        message_id,
                        chat_id,
                        sender,
                        content,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        2,
                        1,
                        "assistant",
                        "Ja, we bespraken de trapscène.",
                        old_date
                    )
                )

                db.execute(
                    """
                    INSERT INTO messages
                    (
                        message_id,
                        chat_id,
                        sender,
                        content,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        3,
                        2,
                        "user",
                        "Nico vond een paarse gitaar "
                        "in de garage.",
                        old_date
                    )
                )

        self.hogwarts = 1
        self.chaos = 2
        self.episode_id = 1
        self.old_version = 1
        self.new_version = 2

    # ======================================
    # TEST 1
    # ======================================

    def test_01_exacte_aflevering(self):

        self.assertEqual(
            astra_chat.detect_episode(
                "Weet je nog seizoen 1 aflevering 2?"
            ),
            (1, 2)
        )

        self.assertEqual(
            astra_chat.detect_episode(
                "season 1 episode 2"
            ),
            (1, 2)
        )

        self.assertEqual(
            astra_chat.detect_episode(
                "episode 2 season 1"
            ),
            (1, 2)
        )

    # ======================================
    # TEST 2
    # ======================================

    def test_02_juiste_canon(self):

        row = astra_chat.get_canon(
            self.hogwarts,
            1,
            2
        )

        self.assertIsNotNone(row)

        self.assertEqual(
            row[0],
            self.new_version
        )

        self.assertIn(
            "Iedereen lachte behalve Nico",
            row[2]
        )

        self.assertNotIn(
            "bibliotheek",
            row[2]
        )

    # ======================================
    # TEST 3
    # ======================================

    def test_03_oude_berichten(self):

        results = (
            astra_chat.search_old_conversations(
                self.hogwarts,
                "Emma trap Nico",
                limit=10
            )
        )

        self.assertTrue(
            any(
                "Emma van de trap" in item["content"]
                for item in results
            )
        )

    # ======================================
    # TEST 4
    # ======================================

    def test_04_au_isolatie(self):

        hogwarts = (
            astra_chat.search_old_conversations(
                self.hogwarts,
                "paarse gitaar garage",
                limit=20
            )
        )

        chaos = (
            astra_chat.search_old_conversations(
                self.chaos,
                "paarse gitaar garage",
                limit=20
            )
        )

        self.assertFalse(
            any(
                "paarse gitaar" in item["content"]
                for item in hogwarts
            )
        )

        self.assertTrue(
            any(
                "paarse gitaar" in item["content"]
                for item in chaos
            )
        )

    # ======================================
    # TEST 5
    # ======================================

    def test_05_canon_wisselen(self):

        with closing(
            sqlite3.connect(self.test_db)
        ) as db:

            with db:
                db.execute(
                    """
                    UPDATE episode_versions
                    SET is_canon = 0
                    WHERE episode_id = 1
                    """
                )

                db.execute(
                    """
                    UPDATE episode_versions
                    SET is_canon = 1
                    WHERE version_id = 1
                    """
                )

        row = astra_chat.get_canon(
            self.hogwarts,
            1,
            2
        )

        self.assertEqual(
            row[0],
            self.old_version
        )

        self.assertIn(
            "bibliotheek",
            row[2]
        )

        # De canonzoekfunctie moet ook
        # de nieuwe actieve versie gebruiken.
        semantic_memory.update_episode_embeddings(
            self.hogwarts
        )

        results = (
            semantic_memory.search_episode_canon(
                self.hogwarts,
                "bibliotheek",
                limit=10
            )
        )

        self.assertTrue(
            results,
            "Geen canonresultaten gevonden. "
            "Controleer de episode_chunks-index."
        )

        self.assertTrue(
            all(
                item["version_id"] == self.old_version
                for item in results
            )
        )

    # ======================================
    # TEST 6
    # ======================================

    def test_06_ontbrekende_aflevering(self):

        row = astra_chat.get_canon(
            self.hogwarts,
            9,
            99
        )

        self.assertIsNone(row)

        evidence, count, exact = (
            astra_chat.build_evidence(
                self.hogwarts,
                "Weet je nog seizoen 9 aflevering 99?",
                budget=1200
            )
        )

        self.assertFalse(exact)

        self.assertIn(
            "geen opgeslagen actieve canonversie",
            evidence.lower()
        )

    # ======================================
    # TEST 7
    # ======================================

    def test_07_oude_berichten_bijwerken(self):

        astra_chat.index_old_messages(
            self.hogwarts
        )

        with closing(
            sqlite3.connect(self.test_db)
        ) as db:

            with db:
                db.execute(
                    """
                    UPDATE messages
                    SET content = ?
                    WHERE message_id = 1
                    """,
                    (
                        "Emma ontdekte een verborgen "
                        "torenkamer.",
                    )
                )

        astra_chat.index_old_messages(
            self.hogwarts
        )

        results = (
            astra_chat.search_old_conversations(
                self.hogwarts,
                "verborgen torenkamer",
                limit=20
            )
        )

        self.assertTrue(
            any(
                "verborgen torenkamer" in item["content"]
                for item in results
            )
        )

        self.assertFalse(
            any(
                item["message_id"] == 1
                and "trap viel" in item["content"]
                for item in results
            )
        )

    # ======================================
    # TEST 8
    # ======================================

    def test_08_chatmodel_en_opslaan(self):

        with patch.object(
            astra_chat,
            "call_ollama",
            return_value=(
                "OH JAAA! Nico lachte niet."
            )
        ) as fake_ollama:

            result = astra_chat.chat_with_astra(
                self.hogwarts,
                "Weet je nog seizoen 1 aflevering 2?"
            )

        self.assertTrue(
            fake_ollama.called
        )

        self.assertTrue(
            result["exact_episode"]
        )

        self.assertIn(
            "Nico lachte niet",
            result["answer"]
        )

        with closing(
            sqlite3.connect(self.test_db)
        ) as db:

            rows = db.execute(
                """
                SELECT sender, content
                FROM messages
                WHERE chat_id = ?
                ORDER BY message_id DESC
                LIMIT 2
                """,
                (self.hogwarts,)
            ).fetchall()

        self.assertEqual(
            rows[0][0],
            "assistant"
        )

        self.assertEqual(
            rows[1][0],
            "user"
        )

    # ======================================
    # TEST 9
    # ======================================

    def test_09_niet_canon_uitgesloten(self):

        semantic_memory.update_episode_embeddings(
            self.hogwarts
        )

        results = (
            semantic_memory.search_episode_canon(
                self.hogwarts,
                "Emma Nico trap",
                limit=20
            )
        )

        self.assertTrue(
            results,
            "Geen canonfragmenten gevonden. "
            "De embeddingindex is mogelijk leeg."
        )

        self.assertTrue(
            all(
                item["version_id"] == self.new_version
                for item in results
            )
        )


# ==========================================
# START
# ==========================================

if __name__ == "__main__":

    print("\n" + "=" * 55)
    print("ASTRA AI - MISSIE 4 + 5 GEHEUGENTEST")
    print("Jouw echte database blijft onaangeraakt.")
    print("=" * 55 + "\n")

    unittest.main(
        verbosity=2
    )
