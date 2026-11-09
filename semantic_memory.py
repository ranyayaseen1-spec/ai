
"""
ASTRA AI - MISSIE 4
SEMANTISCH EN CANONBEWUST LANGETERMIJNGEHEUGEN

Werkt samen met:
- database.py
- astra_chat.py
- test_astra_geheugen.py

Start vanuit de projecthoofdmap:
    python ai/semantic_memory.py

Deze versie:
- Repareert 1D/2D-embeddingverschillen.
- Sluit SQLite-verbindingen expliciet.
- Indexeert alle opgeslagen afleveringsversies.
- Zoekt uitsluitend in actieve canonafleveringen.
- Houdt AU-chats gescheiden.
- Herindexeert gewijzigde teksten automatisch.
- Verwijdert geen opgeslagen verhalen.
"""

import hashlib
import sqlite3

from contextlib import contextmanager

import numpy as np
from sentence_transformers import SentenceTransformer

from database import DB_PATH, initialize_database


MODEL_NAME = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)

CHUNK_SIZE = 850
CHUNK_OVERLAP = 120
EMBEDDING_BATCH_SIZE = 48

_model = None


# ==========================================
# DATABASEVERBINDING
# ==========================================

@contextmanager
def _connect():
    """
    Sluit de verbinding altijd expliciet.

    Dit voorkomt dat SQLite-bestanden onnodig
    vergrendeld blijven op Windows.
    """
    connection = sqlite3.connect(DB_PATH)

    try:
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        with connection:
            yield connection

    finally:
        connection.close()


# ==========================================
# EMBEDDINGMODEL
# ==========================================

def get_model():
    global _model

    if _model is None:
        print(
            "Astra's geheugenmodel wordt geladen..."
        )

        _model = SentenceTransformer(
            MODEL_NAME
        )

    return _model


def _normalize_vector(vector):
    """
    Zet een embedding om naar één 1D-vector.

    Accepteert:
        (384,)
        (1, 384)

    Werkt daardoor met SentenceTransformer
    én met het nepmodel van onze tests.
    """
    vector = np.asarray(
        vector,
        dtype=np.float32
    )

    if vector.ndim == 2 and vector.shape[0] == 1:
        vector = vector[0]

    if vector.ndim != 1:
        raise ValueError(
            "Ongeldige embeddingvorm: "
            f"{vector.shape}"
        )

    return vector


def _encode_query(text):
    """
    Gebruik een lijst met één zoekvraag.

    Zo is de uitvoer voorspelbaar voor
    verschillende embeddingmodellen.
    """
    vectors = get_model().encode(
        [text],
        normalize_embeddings=True,
        convert_to_numpy=True
    )

    return _normalize_vector(
        vectors[0]
    )


def _encode(texts):
    """
    Maak embeddings voor een lijst teksten.

    Retourneert bytes die in SQLite
    opgeslagen kunnen worden.
    """
    if not texts:
        return []

    blobs = []

    for start in range(
        0,
        len(texts),
        EMBEDDING_BATCH_SIZE
    ):
        batch = texts[
            start:start + EMBEDDING_BATCH_SIZE
        ]

        vectors = get_model().encode(
            batch,
            normalize_embeddings=True,
            convert_to_numpy=True
        )

        for vector in vectors:
            normalized = _normalize_vector(
                vector
            )

            blobs.append(
                normalized.tobytes()
            )

    return blobs


def _score(query_vector, embedding_blob):
    """
    Vergelijk twee genormaliseerde embeddings.

    Incompatibele oude embeddings worden
    veilig overgeslagen.
    """
    query_vector = _normalize_vector(
        query_vector
    )

    stored_vector = np.frombuffer(
        embedding_blob,
        dtype=np.float32
    )

    if stored_vector.shape != query_vector.shape:
        return None

    return float(
        np.dot(
            query_vector,
            stored_vector
        )
    )


# ==========================================
# HULPFUNCTIES
# ==========================================

def _hash(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def _chunks(
    text,
    size=CHUNK_SIZE,
    overlap=CHUNK_OVERLAP
):
    """
    Verdeel lange teksten in overlappende
    stukken zonder verhaalinhoud te wijzigen.
    """
    if not text or not text.strip():
        return []

    if size <= 0 or not 0 <= overlap < size:
        raise ValueError(
            "Ongeldige chunkgrootte of overlap."
        )

    text = text.strip()

    result = []
    start = 0

    while start < len(text):
        end = min(
            start + size,
            len(text)
        )

        if end < len(text):
            boundary = max(
                text.rfind(
                    "\n",
                    start + size // 2,
                    end
                ),
                text.rfind(
                    " ",
                    start + size // 2,
                    end
                )
            )

            if boundary > start:
                end = boundary + 1

        part = text[start:end].strip()

        if part:
            result.append(part)

        if end == len(text):
            break

        start = max(
            start + 1,
            end - overlap
        )

    return result


# ==========================================
# GEHEUGENTABELLEN
# ==========================================

def initialize_semantic_memory():
    """
    Maak ontbrekende indextabellen aan.

    Bestaande afleveringen en berichten
    blijven onaangeraakt.
    """
    initialize_database()

    with _connect() as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            memory_embeddings (
                memory_id INTEGER PRIMARY KEY,
                model_name TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding BLOB NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            episode_chunks (
                version_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                model_name TEXT NOT NULL,
                embedding BLOB NOT NULL,

                PRIMARY KEY (
                    version_id,
                    chunk_index
                ),

                FOREIGN KEY (version_id)
                    REFERENCES episode_versions(
                        version_id
                    )
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_episode_chunks_version
            ON episode_chunks(version_id)
            """
        )


# ==========================================
# LOSSE HERINNERINGEN INDEXEREN
# ==========================================

def update_memory_embeddings(chat_id):
    initialize_semantic_memory()

    with _connect() as connection:

        memories = connection.execute(
            """
            SELECT memory_id, content
            FROM memories
            WHERE chat_id = ?
            """,
            (chat_id,)
        ).fetchall()

        stored = connection.execute(
            """
            SELECT
                emb.memory_id,
                emb.model_name,
                emb.content_hash

            FROM memory_embeddings AS emb

            JOIN memories AS m
                ON m.memory_id = emb.memory_id

            WHERE m.chat_id = ?
            """,
            (chat_id,)
        ).fetchall()

    existing = {
        memory_id: (
            model_name,
            content_hash
        )
        for memory_id, model_name, content_hash
        in stored
    }

    pending = []

    for memory_id, content in memories:

        if not content:
            continue

        digest = _hash(content)

        if existing.get(memory_id) != (
            MODEL_NAME,
            digest
        ):
            pending.append(
                (
                    memory_id,
                    content,
                    digest
                )
            )

    blobs = _encode(
        [item[1] for item in pending]
    )

    with _connect() as connection:

        for (
            memory_id,
            content,
            digest
        ), blob in zip(pending, blobs):

            connection.execute(
                """
                INSERT OR REPLACE INTO
                memory_embeddings (
                    memory_id,
                    model_name,
                    content_hash,
                    embedding
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    memory_id,
                    MODEL_NAME,
                    digest,
                    blob
                )
            )

        # Alleen indexgegevens van verwijderde
        # herinneringen opruimen.
        connection.execute(
            """
            DELETE FROM memory_embeddings
            WHERE NOT EXISTS (
                SELECT 1
                FROM memories AS m
                WHERE m.memory_id =
                    memory_embeddings.memory_id
            )
            """
        )

    return len(pending)


# ==========================================
# AFLEVERINGEN INDEXEREN
# ==========================================

def update_episode_embeddings(chat_id):
    """
    Indexeer alle versies van afleveringen.

    Bij het zoeken wordt alleen de actieve
    canonversie gebruikt.

    Een canonwissel vereist dus geen
    verwijdering van oude verhaalversies.
    """
    initialize_semantic_memory()

    with _connect() as connection:

        versions = connection.execute(
            """
            SELECT
                v.version_id,
                COALESCE(v.transcript_text, '')

            FROM episode_versions AS v

            JOIN episodes AS ep
                ON ep.episode_id = v.episode_id

            WHERE ep.chat_id = ?
            """,
            (chat_id,)
        ).fetchall()

        stored = connection.execute(
            """
            SELECT
                c.version_id,
                c.chunk_index,
                c.content_hash,
                c.model_name

            FROM episode_chunks AS c

            JOIN episode_versions AS v
                ON v.version_id = c.version_id

            JOIN episodes AS ep
                ON ep.episode_id = v.episode_id

            WHERE ep.chat_id = ?
            """,
            (chat_id,)
        ).fetchall()

    existing = {}

    for (
        version_id,
        index,
        digest,
        model_name
    ) in stored:

        existing.setdefault(
            version_id,
            {}
        )[index] = (
            digest,
            model_name
        )

    replacements = []

    for version_id, transcript in versions:

        pieces = _chunks(
            transcript
        )

        expected = {
            index: (
                _hash(piece),
                MODEL_NAME
            )
            for index, piece
            in enumerate(pieces)
        }

        if existing.get(
            version_id,
            {}
        ) != expected:

            replacements.append(
                (
                    version_id,
                    pieces
                )
            )

    all_texts = [
        piece
        for _, pieces in replacements
        for piece in pieces
    ]

    blobs = iter(
        _encode(all_texts)
    )

    with _connect() as connection:

        for version_id, pieces in replacements:

            connection.execute(
                """
                DELETE FROM episode_chunks
                WHERE version_id = ?
                """,
                (version_id,)
            )

            for index, piece in enumerate(pieces):

                connection.execute(
                    """
                    INSERT INTO episode_chunks (
                        version_id,
                        chunk_index,
                        content,
                        content_hash,
                        model_name,
                        embedding
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        index,
                        piece,
                        _hash(piece),
                        MODEL_NAME,
                        next(blobs)
                    )
                )

        # Verwijder alleen verweesde indexregels.
        connection.execute(
            """
            DELETE FROM episode_chunks
            WHERE NOT EXISTS (
                SELECT 1
                FROM episode_versions AS v
                WHERE v.version_id =
                    episode_chunks.version_id
            )
            """
        )

    return len(replacements)


# ==========================================
# SEMANTISCH ZOEKEN IN HERINNERINGEN
# ==========================================

def search_memories_semantic(
    chat_id,
    query,
    limit=5,
    canon_only=True
):
    """
    Zoek herinneringen in één AU.

    canon_only=True:
    - Algemene herinneringen.
    - Herinneringen gekoppeld aan actieve canon.

    canon_only=False:
    - Alle herinneringen van deze AU.
    """
    if (
        not isinstance(query, str)
        or not query.strip()
        or limit <= 0
    ):
        return []

    update_memory_embeddings(
        chat_id
    )

    query_vector = _encode_query(
        query
    )

    with _connect() as connection:

        rows = connection.execute(
            """
            SELECT
                m.memory_id,
                m.content,
                m.season,
                m.episode,
                m.version,
                emb.embedding,

                EXISTS (
                    SELECT 1

                    FROM episodes AS ep

                    JOIN episode_versions AS v
                        ON v.episode_id = ep.episode_id

                    WHERE ep.chat_id = m.chat_id
                      AND ep.season_id = m.season
                      AND ep.episode_number = m.episode
                      AND v.version_number = m.version
                      AND v.is_canon = 1
                ) AS active_canon

            FROM memories AS m

            JOIN memory_embeddings AS emb
                ON emb.memory_id = m.memory_id

            WHERE m.chat_id = ?
              AND emb.model_name = ?
            """,
            (
                chat_id,
                MODEL_NAME
            )
        ).fetchall()

    results = []

    for (
        memory_id,
        content,
        season,
        episode,
        version,
        blob,
        active_canon
    ) in rows:

        is_general = (
            season is None
            and episode is None
            and version is None
        )

        if canon_only and not (
            is_general or active_canon
        ):
            continue

        score = _score(
            query_vector,
            blob
        )

        if score is None:
            continue

        results.append({
            "source": "memory",
            "memory_id": memory_id,
            "content": content,
            "season": season,
            "episode": episode,
            "version": version,
            "score": round(score, 4)
        })

    results.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return results[:limit]


# ==========================================
# ZOEKEN IN ACTIEVE CANON
# ==========================================

def search_episode_canon(
    chat_id,
    query,
    limit=5
):
    """
    Doorzoek uitsluitend de actieve
    canonafleveringen van één AU.
    """
    if (
        not isinstance(query, str)
        or not query.strip()
        or limit <= 0
    ):
        return []

    update_episode_embeddings(
        chat_id
    )

    query_vector = _encode_query(
        query
    )

    with _connect() as connection:

        rows = connection.execute(
            """
            SELECT
                ep.season_id,
                ep.episode_number,
                v.version_number,
                c.version_id,
                c.chunk_index,
                c.content,
                c.embedding

            FROM episode_chunks AS c

            JOIN episode_versions AS v
                ON v.version_id = c.version_id

            JOIN episodes AS ep
                ON ep.episode_id = v.episode_id

            WHERE ep.chat_id = ?
              AND v.is_canon = 1
              AND c.model_name = ?
            """,
            (
                chat_id,
                MODEL_NAME
            )
        ).fetchall()

    results = []

    for (
        season,
        episode,
        version,
        version_id,
        chunk_index,
        content,
        blob
    ) in rows:

        score = _score(
            query_vector,
            blob
        )

        if score is None:
            continue

        results.append({
            "source": "episode",
            "version_id": version_id,
            "chunk_index": chunk_index,
            "content": content,
            "season": season,
            "episode": episode,
            "version": version,
            "score": round(score, 4)
        })

    results.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return results[:limit]


# ==========================================
# ASTRA'S COMPLETE GEHEUGENZOEKFUNCTIE
# ==========================================

def search_astra_memory(
    chat_id,
    query,
    limit=5,
    canon_only=True
):
    """
    Combineert losse herinneringen en
    actieve canonafleveringen.

    Oudere chatberichten worden apart
    door astra_chat.py doorzocht.
    """
    if (
        not isinstance(query, str)
        or not query.strip()
        or limit <= 0
    ):
        return []

    memory_results = search_memories_semantic(
        chat_id=chat_id,
        query=query,
        limit=limit,
        canon_only=canon_only
    )

    episode_results = search_episode_canon(
        chat_id=chat_id,
        query=query,
        limit=limit
    )

    results = (
        memory_results
        + episode_results
    )

    results.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return results[:limit]


# ==========================================
# HANDMATIGE CONTROLE
# ==========================================

if __name__ == "__main__":

    initialize_semantic_memory()

    print(
        "\nAstra's geheugen is klaar!"
    )

    print(
        "\nZoekresultaten voor chat 1:"
    )

    results = search_astra_memory(
        chat_id=1,
        query="Welke verborgen plek werd ontdekt?",
        limit=5
    )

    if not results:
        print(
            "Geen canonherinneringen of "
            "canonafleveringen gevonden."
        )

    for result in results:

        print(
            f"\nBron: {result['source']}"
            f"\nHerinnering: {result['content']}"
            f"\nRelevantie: {result['score']}"
            f"\nSeizoen: {result['season']}"
            f"\nAflevering: {result['episode']}"
            f"\nVersie: {result['version']}"
        )
