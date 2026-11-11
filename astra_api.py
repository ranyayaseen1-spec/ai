
"""
ASTRA AI - COMPLETE LOKALE BACKEND API

Bestand: ai/astra_api.py

Starten:
    python ai/astra_api.py

API-documentatie:
    http://127.0.0.1:8001/docs

Functies:
- AU-chats
- Chatgeschiedenis
- Afleveringen
- Canonversies
- Langetermijngeheugen
- Semantisch zoeken
- Verhalen importeren
- Dynamische Canon Engine
- Aanpasbare verboden-zinnenregels

Bestaande verhalen worden niet verwijderd.
"""

import sqlite3

from contextlib import closing
from datetime import datetime, timezone
from threading import RLock
from typing import Optional

import uvicorn

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from database import DB_PATH, initialize_database

from semantic_memory import (
    initialize_semantic_memory,
    search_astra_memory,
)

from astra_chat import (
    initialize_chat_search,
    chat_with_astra,
    search_old_conversations,
)


# ==========================================
# INSTELLINGEN
# ==========================================

API_HOST = "127.0.0.1"
API_PORT = 8001

DATABASE_LOCK = RLock()


# ==========================================
# FASTAPI
# ==========================================

app = FastAPI(
    title="Astra AI API",
    description=(
        "Lokale backend voor Astra AI: "
        "AU-chats, afleveringen, canon, "
        "berichten, langetermijngeheugen, "
        "verhaalimports en dynamische regels."
    ),
    version="1.0.0",
)


# ==========================================
# MISSIE 7 - VERHALEN IMPORTEREN
# ==========================================

from astra_import import router as import_router
app.include_router(import_router)



# ==========================================
# MISSIE 7 - DYNAMISCHE CANON ENGINE
# ==========================================

from astra_canon_engine import install_astra_engine
install_astra_engine(app)

from astra_callbacks import router as callbacks_router
app.include_router(callbacks_router)

# ==========================================
# DATABASE
# ==========================================

def connect():

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def now():

    return datetime.now(
        timezone.utc
    ).isoformat()


def as_dict(row):

    return dict(row) if row else None


def require_chat(connection, chat_id):

    row = connection.execute(
        """
        SELECT
            chat_id,
            title,
            created_at,
            is_pinned
        FROM conversations
        WHERE chat_id = ?
        """,
        (chat_id,),
    ).fetchone()

    if row is None:

        raise HTTPException(
            status_code=404,
            detail="Deze AU-chat bestaat niet.",
        )

    return row


def require_episode(connection, episode_id):

    row = connection.execute(
        """
        SELECT
            episode_id,
            chat_id,
            season_id,
            episode_number
        FROM episodes
        WHERE episode_id = ?
        """,
        (episode_id,),
    ).fetchone()

    if row is None:

        raise HTTPException(
            status_code=404,
            detail="Deze aflevering bestaat niet.",
        )

    return row


def require_version(connection, version_id):

    row = connection.execute(
        """
        SELECT
            version_id,
            episode_id,
            version_number,
            transcript_text,
            is_canon
        FROM episode_versions
        WHERE version_id = ?
        """,
        (version_id,),
    ).fetchone()

    if row is None:

        raise HTTPException(
            status_code=404,
            detail="Deze versie bestaat niet.",
        )

    return row


# ==========================================
# REQUESTMODELLEN
# ==========================================

class CreateChat(BaseModel):

    title: str = Field(
        min_length=1,
        max_length=200,
    )


class UpdateChat(BaseModel):

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    is_pinned: Optional[bool] = None


class SendMessage(BaseModel):

    content: str = Field(
        min_length=1,
        max_length=100000,
    )


class CreateEpisode(BaseModel):

    season: int = Field(ge=1)
    episode: int = Field(ge=1)


class CreateVersion(BaseModel):

    version_number: str = Field(
        min_length=1,
        max_length=100,
    )

    transcript_text: str = ""

    is_canon: bool = False


class SearchRequest(BaseModel):

    query: str = Field(
        min_length=1,
        max_length=10000,
    )

    limit: int = Field(
        default=10,
        ge=1,
        le=100,
    )


class CreateMemory(BaseModel):

    content: str = Field(
        min_length=1,
    )

    season: Optional[int] = Field(
        default=None,
        ge=1,
    )

    episode: Optional[int] = Field(
        default=None,
        ge=1,
    )

    version: Optional[str] = None


# ==========================================
# SERVERSTATUS
# ==========================================

@app.get("/")
def home():

    return {
        "name": "Astra AI",
        "status": "online",
        "mission": 7,
        "message": "Astra's backend is actief!",
        "docs": "/docs",
        "canon_engine": "/astra/status",
    }


@app.get("/health")
def health():

    try:

        with closing(connect()) as db:
            db.execute("SELECT 1").fetchone()

        return {
            "status": "ok",
            "database": "connected",
        }

    except sqlite3.Error:

        raise HTTPException(
            status_code=503,
            detail="Database niet bereikbaar.",
        )


# ==========================================
# AU-CHATS
# ==========================================

@app.get("/chats")
def list_chats():

    with closing(connect()) as db:

        rows = db.execute(
            """
            SELECT
                chat_id,
                title,
                created_at,
                is_pinned
            FROM conversations
            ORDER BY
                is_pinned DESC,
                chat_id DESC
            """
        ).fetchall()

    return {
        "chats": [
            dict(row)
            for row in rows
        ]
    }


@app.post("/chats", status_code=201)
def create_chat(data: CreateChat):

    title = data.title.strip()

    if not title:

        raise HTTPException(
            status_code=422,
            detail="De AU-naam mag niet leeg zijn.",
        )

    with DATABASE_LOCK:

        with closing(connect()) as db:

            with db:

                cursor = db.execute(
                    """
                    INSERT INTO conversations (
                        title,
                        created_at,
                        is_pinned
                    )
                    VALUES (?, ?, 0)
                    """,
                    (title, now()),
                )

                chat_id = cursor.lastrowid

    return {
        "chat_id": chat_id,
        "title": title,
        "is_pinned": False,
    }


@app.get("/chats/{chat_id}")
def get_chat(chat_id: int):

    with closing(connect()) as db:

        row = require_chat(
            db,
            chat_id,
        )

    return dict(row)


@app.patch("/chats/{chat_id}")
def update_chat(
    chat_id: int,
    data: UpdateChat,
):

    changes = data.model_dump(
        exclude_unset=True
    )

    if not changes:

        raise HTTPException(
            status_code=422,
            detail="Geen wijzigingen opgegeven.",
        )

    if "title" in changes:

        changes["title"] = (
            changes["title"].strip()
        )

        if not changes["title"]:

            raise HTTPException(
                status_code=422,
                detail="De AU-naam mag niet leeg zijn.",
            )

    if "is_pinned" in changes:

        changes["is_pinned"] = int(
            changes["is_pinned"]
        )

    fields = []
    values = []

    for key, value in changes.items():

        fields.append(f"{key} = ?")
        values.append(value)

    values.append(chat_id)

    with DATABASE_LOCK:

        with closing(connect()) as db:

            with db:

                require_chat(
                    db,
                    chat_id,
                )

                db.execute(
                    f"""
                    UPDATE conversations
                    SET {", ".join(fields)}
                    WHERE chat_id = ?
                    """,
                    values,
                )

            updated = require_chat(
                db,
                chat_id,
            )

    return dict(updated)


# ==========================================
# BERICHTEN
# ==========================================

@app.get("/chats/{chat_id}/messages")
def get_messages(
    chat_id: int,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    before_id: Optional[int] = Query(
        default=None,
        ge=1,
    ),
):

    with closing(connect()) as db:

        require_chat(
            db,
            chat_id,
        )

        if before_id is None:

            rows = db.execute(
                """
                SELECT
                    message_id,
                    chat_id,
                    sender,
                    content,
                    created_at
                FROM messages
                WHERE chat_id = ?
                ORDER BY message_id DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()

        else:

            rows = db.execute(
                """
                SELECT
                    message_id,
                    chat_id,
                    sender,
                    content,
                    created_at
                FROM messages
                WHERE chat_id = ?
                  AND message_id < ?
                ORDER BY message_id DESC
                LIMIT ?
                """,
                (
                    chat_id,
                    before_id,
                    limit,
                ),
            ).fetchall()

    messages = [
        dict(row)
        for row in reversed(rows)
    ]

    return {
        "chat_id": chat_id,
        "messages": messages,
        "next_before_id": (
            messages[0]["message_id"]
            if messages
            else None
        ),
    }


# ==========================================
# BESTAANDE CHATROUTE
# ==========================================

@app.post("/chats/{chat_id}/chat")
def send_chat_message(
    chat_id: int,
    data: SendMessage,
):

    """
    Bestaande chatfunctie.

    LET OP:
    De nieuwe Canon Engine schermt deze
    route af via middleware.

    Gebruik voortaan POST /astra/chat
    met een expliciete modus.
    """

    question = data.content.strip()

    if not question:

        raise HTTPException(
            status_code=422,
            detail="Bericht mag niet leeg zijn.",
        )

    with closing(connect()) as db:

        require_chat(
            db,
            chat_id,
        )

    try:

        with DATABASE_LOCK:

            result = chat_with_astra(
                chat_id,
                question,
            )

    except (
        RuntimeError,
        sqlite3.Error,
    ) as error:

        raise HTTPException(
            status_code=503,
            detail=(
                "Astra kon nu niet antwoorden. "
                f"Technische melding: {error}"
            ),
        ) from error

    return {
        "chat_id": chat_id,
        "question": question,
        "answer": result["answer"],
        "memory": {
            "sources_used": result["sources"],
            "recent_messages_used": result["history"],
            "exact_canon_episode": result["exact_episode"],
        },
    }


# ==========================================
# AFLEVERINGEN
# ==========================================

@app.get("/chats/{chat_id}/episodes")
def list_episodes(chat_id: int):

    with closing(connect()) as db:

        require_chat(
            db,
            chat_id,
        )

        rows = db.execute(
            """
            SELECT
                ep.episode_id,
                ep.chat_id,
                ep.season_id AS season,
                ep.episode_number AS episode,
                v.version_id AS canon_version_id,
                v.version_number AS canon_version
            FROM episodes AS ep

            LEFT JOIN episode_versions AS v
                ON v.episode_id = ep.episode_id
               AND v.is_canon = 1

            WHERE ep.chat_id = ?

            ORDER BY
                ep.season_id,
                ep.episode_number
            """,
            (chat_id,),
        ).fetchall()

    return {
        "chat_id": chat_id,
        "episodes": [
            dict(row)
            for row in rows
        ],
    }


@app.post(
    "/chats/{chat_id}/episodes",
    status_code=201,
)
def create_episode(
    chat_id: int,
    data: CreateEpisode,
):

    with DATABASE_LOCK:

        with closing(connect()) as db:

            with db:

                require_chat(
                    db,
                    chat_id,
                )

                existing = db.execute(
                    """
                    SELECT episode_id
                    FROM episodes
                    WHERE chat_id = ?
                      AND season_id = ?
                      AND episode_number = ?
                    """,
                    (
                        chat_id,
                        data.season,
                        data.episode,
                    ),
                ).fetchone()

                if existing:

                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Deze aflevering bestaat "
                            "al in deze AU."
                        ),
                    )

                cursor = db.execute(
                    """
                    INSERT INTO episodes (
                        chat_id,
                        season_id,
                        episode_number
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        chat_id,
                        data.season,
                        data.episode,
                    ),
                )

                episode_id = cursor.lastrowid

    return {
        "episode_id": episode_id,
        "chat_id": chat_id,
        "season": data.season,
        "episode": data.episode,
    }


@app.get("/episodes/{episode_id}")
def get_episode(episode_id: int):

    with closing(connect()) as db:

        episode = require_episode(
            db,
            episode_id,
        )

        versions = db.execute(
            """
            SELECT
                version_id,
                version_number,
                is_canon
            FROM episode_versions
            WHERE episode_id = ?
            ORDER BY version_id
            """,
            (episode_id,),
        ).fetchall()

    return {
        **dict(episode),
        "versions": [
            dict(row)
            for row in versions
        ],
    }


# ==========================================
# AFLEVERINGSVERSIES
# ==========================================

@app.post(
    "/episodes/{episode_id}/versions",
    status_code=201,
)
def create_version(
    episode_id: int,
    data: CreateVersion,
):

    version_name = (
        data.version_number.strip()
    )

    if not version_name:

        raise HTTPException(
            status_code=422,
            detail="Versienummer mag niet leeg zijn.",
        )

    with DATABASE_LOCK:

        with closing(connect()) as db:

            with db:

                require_episode(
                    db,
                    episode_id,
                )

                existing = db.execute(
                    """
                    SELECT version_id
                    FROM episode_versions
                    WHERE episode_id = ?
                      AND version_number = ?
                    """,
                    (
                        episode_id,
                        version_name,
                    ),
                ).fetchone()

                if existing:

                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Dit versienummer bestaat "
                            "al voor deze aflevering."
                        ),
                    )

                if data.is_canon:

                    db.execute(
                        """
                        UPDATE episode_versions
                        SET is_canon = 0
                        WHERE episode_id = ?
                        """,
                        (episode_id,),
                    )

                cursor = db.execute(
                    """
                    INSERT INTO episode_versions (
                        episode_id,
                        version_number,
                        transcript_text,
                        is_canon
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        episode_id,
                        version_name,
                        data.transcript_text,
                        int(data.is_canon),
                    ),
                )

                version_id = cursor.lastrowid

    return {
        "version_id": version_id,
        "episode_id": episode_id,
        "version_number": version_name,
        "is_canon": data.is_canon,
    }


@app.get("/versions/{version_id}")
def get_version(version_id: int):

    with closing(connect()) as db:

        row = require_version(
            db,
            version_id,
        )

    return dict(row)


@app.put(
    "/episodes/{episode_id}/canon/{version_id}"
)
def switch_canon(
    episode_id: int,
    version_id: int,
):

    with DATABASE_LOCK:

        with closing(connect()) as db:

            with db:

                require_episode(
                    db,
                    episode_id,
                )

                version = require_version(
                    db,
                    version_id,
                )

                if version["episode_id"] != episode_id:

                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Deze versie hoort niet "
                            "bij deze aflevering."
                        ),
                    )

                db.execute(
                    """
                    UPDATE episode_versions
                    SET is_canon = 0
                    WHERE episode_id = ?
                    """,
                    (episode_id,),
                )

                db.execute(
                    """
                    UPDATE episode_versions
                    SET is_canon = 1
                    WHERE version_id = ?
                    """,
                    (version_id,),
                )

    return {
        "success": True,
        "episode_id": episode_id,
        "canon_version_id": version_id,
        "canon_version": version["version_number"],
    }


# ==========================================
# GEHEUGEN OPSLAAN
# ==========================================

@app.post(
    "/chats/{chat_id}/memories",
    status_code=201,
)
def create_memory(
    chat_id: int,
    data: CreateMemory,
):

    content = data.content.strip()

    if not content:

        raise HTTPException(
            status_code=422,
            detail="Herinnering mag niet leeg zijn.",
        )

    with DATABASE_LOCK:

        with closing(connect()) as db:

            with db:

                require_chat(
                    db,
                    chat_id,
                )

                cursor = db.execute(
                    """
                    INSERT INTO memories (
                        chat_id,
                        content,
                        season,
                        episode,
                        version
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        content,
                        data.season,
                        data.episode,
                        data.version,
                    ),
                )

                memory_id = cursor.lastrowid

    return {
        "memory_id": memory_id,
        "chat_id": chat_id,
        "content": content,
        "season": data.season,
        "episode": data.episode,
        "version": data.version,
    }


@app.get("/chats/{chat_id}/memories")
def list_memories(
    chat_id: int,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
):

    with closing(connect()) as db:

        require_chat(
            db,
            chat_id,
        )

        rows = db.execute(
            """
            SELECT
                memory_id,
                content,
                season,
                episode,
                version
            FROM memories
            WHERE chat_id = ?
            ORDER BY memory_id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()

    return {
        "chat_id": chat_id,
        "memories": [
            dict(row)
            for row in rows
        ],
    }


# ==========================================
# GEHEUGEN DOORZOEKEN
# ==========================================

@app.post("/chats/{chat_id}/search")
def search_memory(
    chat_id: int,
    data: SearchRequest,
):

    with closing(connect()) as db:

        require_chat(
            db,
            chat_id,
        )

    try:

        with DATABASE_LOCK:

            canon_results = search_astra_memory(
                chat_id=chat_id,
                query=data.query,
                limit=data.limit,
                canon_only=True,
            )

            old_chat_results = search_old_conversations(
                chat_id=chat_id,
                question=data.query,
                limit=data.limit,
            )

    except (
        RuntimeError,
        sqlite3.Error,
    ) as error:

        raise HTTPException(
            status_code=503,
            detail=(
                "Geheugenzoeken is tijdelijk "
                f"niet beschikbaar: {error}"
            ),
        ) from error

    return {
        "chat_id": chat_id,
        "query": data.query,
        "canon_and_memories": canon_results,
        "old_conversations": old_chat_results,
        "note": (
            "Oude chatberichten zijn niet "
            "automatisch officiële canon."
        ),
    }


# ==========================================
# OPSTARTEN
# ==========================================

def prepare_astra():

    print("\n" + "=" * 50)
    print("ASTRA AI - MISSIE 7")
    print("BACKEND API")
    print("=" * 50)

    print("Database controleren...")
    initialize_database()

    print("Semantisch geheugen voorbereiden...")
    initialize_semantic_memory()

    print("Oude-chatindex voorbereiden...")
    initialize_chat_search()

    print("\nAstra's backend is klaar! 💗")

    print(
        f"Open: http://{API_HOST}:{API_PORT}/docs"
    )

    print(
        "Stoppen: Ctrl + C\n"
    )


if __name__ == "__main__":

    prepare_astra()

    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        log_level="info",
    )


from astra_extensions import install_astra_extensions

install_astra_extensions(app)
