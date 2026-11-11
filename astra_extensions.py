
"""
ASTRA AI - BACKEND UITBREIDINGEN

MISSIE 9.5 - Canonbewijs
MISSIE 10  - Gesprekscontext
MISSIE 13  - Android API voorbereiding

Dit bestand verandert de bestaande
Missie 9.4-routes niet.
"""

import sqlite3

from contextlib import closing
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import DB_PATH


router = APIRouter(
    prefix="/astra/v2",
    tags=["Astra uitbreidingen"],
)


# ============================================
# DATABASE
# ============================================

def db_open():
    db = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    return db


def chat_exists(db, chat_id):
    row = db.execute(
        """
        SELECT 1
        FROM conversations
        WHERE chat_id = ?
        """,
        (chat_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Chat niet gevonden",
        )


# ============================================
# REQUESTMODELLEN
# ============================================

class MemoryQuery(BaseModel):
    chat_id: int = Field(gt=0)

    season: Optional[int] = Field(
        default=None,
        ge=1,
    )

    episode: Optional[int] = Field(
        default=None,
        ge=1,
    )

    question: str = Field(
        min_length=1,
        max_length=10000,
    )

    limit: int = Field(
        default=5,
        ge=1,
        le=20,
    )


class ConversationQuery(BaseModel):
    chat_id: int = Field(gt=0)

    limit: int = Field(
        default=12,
        ge=1,
        le=40,
    )


# ============================================
# MISSIE 9.5 - CANON OPHALEN
# ============================================

def prior_canon_rows(
    db,
    chat_id,
    season,
    episode,
):
    sql = """
        SELECT
            e.season_id AS season,
            e.episode_number AS episode,
            v.version_id,
            v.transcript_text AS excerpt
        FROM episodes e
        JOIN episode_versions v
          ON e.episode_id = v.episode_id
        WHERE e.chat_id = ?
          AND v.is_canon = 1
          AND v.transcript_text IS NOT NULL
          AND TRIM(v.transcript_text) != ''
    """

    args = [chat_id]

    if season is not None:
        if episode is None:
            sql += """
                AND e.season_id < ?
            """

            args.append(season)

        else:
            sql += """
                AND (
                    e.season_id < ?
                    OR (
                        e.season_id = ?
                        AND e.episode_number < ?
                    )
                )
            """

            args.extend([
                season,
                season,
                episode,
            ])

    sql += """
        ORDER BY
            e.season_id,
            e.episode_number
    """

    return db.execute(
        sql,
        args,
    ).fetchall()


@router.post("/evidence")
def evidence(data: MemoryQuery):
    """
    Zoek originele canonfragmenten.

    Een gevonden fragment is bewijs
    van de opgeslagen scripttekst,
    niet automatisch bewijs dat een
    interpretatie correct is.
    """

    from astra_canon_engine import (
        find_canon_evidence,
    )

    with closing(db_open()) as db:
        chat_exists(
            db,
            data.chat_id,
        )

        rows = prior_canon_rows(
            db,
            data.chat_id,
            data.season,
            data.episode,
        )

        episodes = [
            {
                "season_id": row["season"],
                "episode_number": row["episode"],
                "version_id": row["version_id"],
                "version_number": str(
                    row["version_id"]
                ),
                "transcript_text": row["excerpt"],
            }
            for row in rows
        ]

    found = find_canon_evidence(
        data.question,
        episodes,
    )

    return {
        "chat_id": data.chat_id,
        "question": data.question,
        "evidence": [
            {
                **item,
                "claim_verified": False,
                "connection_verified": False,
            }
            for item in found[:data.limit]
        ],
        "notice": (
            "Dit zijn canonfragmenten, "
            "geen automatisch bewezen "
            "conclusies."
        ),
    }


# ============================================
# MISSIE 10 - GESPREKSCONTEXT
# ============================================

@router.post("/conversation-context")
def conversation_context(
    data: ConversationQuery,
):
    """
    Haal recente chatberichten op.

    Eerdere AI-antwoorden blijven
    onbetrouwbare chatgeschiedenis.
    Ze worden NIET automatisch canon.
    """

    with closing(db_open()) as db:
        chat_exists(
            db,
            data.chat_id,
        )

        rows = db.execute(
            """
            SELECT
                message_id,
                sender,
                content
            FROM messages
            WHERE chat_id = ?
            ORDER BY message_id DESC
            LIMIT ?
            """,
            (
                data.chat_id,
                data.limit,
            ),
        ).fetchall()

    return {
        "chat_id": data.chat_id,
        "messages": [
            dict(row)
            for row in reversed(rows)
        ],
        "trust": "unverified_chat_history",
        "notice": (
            "Assistant-berichten zijn "
            "geen officiële canon."
        ),
    }


# ============================================
# MISSIE 13 - ANDROID API
# ============================================

@router.get("/mobile/chats")
def mobile_chats():
    """
    Lijst met AU-chats voor de
    toekomstige Android-app.
    """

    with closing(db_open()) as db:
        rows = db.execute(
            """
            SELECT
                chat_id,
                title
            FROM conversations
            ORDER BY chat_id DESC
            """
        ).fetchall()

    return {
        "chats": [
            dict(row)
            for row in rows
        ]
    }


@router.get(
    "/mobile/chats/{chat_id}/episodes"
)
def mobile_episodes(chat_id: int):
    """
    Afleveringen ophalen voor
    een geselecteerde AU-chat.
    """

    with closing(db_open()) as db:
        chat_exists(
            db,
            chat_id,
        )

        rows = db.execute(
            """
            SELECT
                episode_id,
                season_id,
                episode_number
            FROM episodes
            WHERE chat_id = ?
            ORDER BY
                season_id,
                episode_number
            """,
            (chat_id,),
        ).fetchall()

    return {
        "episodes": [
            dict(row)
            for row in rows
        ]
    }


# ============================================
# STATUS
# ============================================

@router.get("/status")
def status():
    return {
        "version": "13-addon-1",
        "evidence": True,
        "conversation_context": True,
        "android_api": True,
        "canon_guaranteed": False,
    }


# ============================================
# INSTALLEREN IN FASTAPI
# ============================================

def install_astra_extensions(app):
    app.include_router(router)
