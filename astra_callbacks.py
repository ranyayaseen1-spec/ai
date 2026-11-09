
"""
ASTRA AI - MISSIE 9
Cross-season callbacks en langetermijnreferenties.

Geen vaste personages, verhaalwoorden of stopwoordenlijsten.
Gebruikt uitsluitend opgeslagen canon uit de gekozen AU.

Routes:
POST /astra/callbacks/analyze
POST /astra/callbacks/chat
GET  /astra/callbacks/status

De chatroute gebruikt de bestaande /astra/chat-engine.
De originele database wordt niet aangepast.
"""

import asyncio
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import DB_PATH


router = APIRouter(
    prefix="/astra/callbacks",
    tags=["Missie 9 - Cross-season memory"]
)


# ============================================================
# INSTELLINGEN
# ============================================================

MIN_SIMILARITY = 0.42
MAX_RESULTS = 5
MAX_FRAGMENT_CHARS = 1200

# Deze instellingen zijn zoekparameters,
# geen hardcoded verhaalwoorden of namen.


# ============================================================
# MODELLEN
# ============================================================

class CallbackRequest(BaseModel):
    chat_id: int = Field(gt=0)
    content: str = Field(min_length=1)
    season: Optional[int] = Field(default=None, ge=1)
    episode: Optional[int] = Field(default=None, ge=1)
    max_results: int = Field(default=5, ge=1, le=20)


class CallbackChatRequest(CallbackRequest):
    mode: str = "creative"


# ============================================================
# DATABASE
# ============================================================

def connect():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


def get_chat(chat_id):
    with closing(connect()) as db:
        row = db.execute(
            """
            SELECT chat_id, title
            FROM conversations
            WHERE chat_id = ?
            """,
            (chat_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Deze AU-chat bestaat niet."
        )

    return dict(row)


def load_canon(chat_id, season=None, episode=None):
    """
    Haal uitsluitend canonversies op.

    Wanneer seizoen en aflevering bekend zijn,
    worden toekomstige afleveringen uitgesloten.
    """

    query = """
        SELECT
            e.season_id AS season,
            e.episode_number AS episode,
            v.version_id,
            v.version_number AS version,
            v.transcript_text AS text
        FROM episodes e
        JOIN episode_versions v
          ON v.episode_id = e.episode_id
        WHERE e.chat_id = ?
          AND v.is_canon = 1
          AND v.transcript_text IS NOT NULL
          AND TRIM(v.transcript_text) != ''
    """

    parameters = [chat_id]

    if season is not None:
        if episode is None:
            query += """
                AND e.season_id < ?
            """
            parameters.append(season)

        else:
            query += """
                AND (
                    e.season_id < ?
                    OR (
                        e.season_id = ?
                        AND e.episode_number < ?
                    )
                )
            """

            parameters.extend([
                season,
                season,
                episode
            ])

    query += """
        ORDER BY
            e.season_id,
            e.episode_number,
            v.version_id
    """

    with closing(connect()) as db:
        rows = db.execute(
            query,
            parameters
        ).fetchall()

    return [dict(row) for row in rows]


# ============================================================
# FRAGMENTEN
# ============================================================

def split_fragments(text):
    """
    Splits op alinea's en langere stukken.

    Geen vaste SCENE-, hoofdstuk- of taalwoorden.
    """

    text = str(text or "").strip()

    if not text:
        return []

    paragraphs = re.split(
        r"\n\s*\n+",
        text
    )

    fragments = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        if len(paragraph) <= MAX_FRAGMENT_CHARS:
            fragments.append(paragraph)
            continue

        # Overlappende stukken voorkomen dat
        # een referentie precies op een grens valt.
        step = MAX_FRAGMENT_CHARS - 200

        for start in range(
            0,
            len(paragraph),
            step
        ):
            fragment = paragraph[
                start:start + MAX_FRAGMENT_CHARS
            ].strip()

            if fragment:
                fragments.append(fragment)

            if start + MAX_FRAGMENT_CHARS >= len(paragraph):
                break

    return fragments


# ============================================================
# TAALONAFHANKELIJKE ZOEKFUNCTIE
# ============================================================

def normalize(text):
    return " ".join(
        str(text).casefold().split()
    )


def character_ngrams(text, size=3):
    """
    Lettergroepen in plaats van een
    hardcoded woordenlijst.
    """

    text = normalize(text)

    if not text:
        return set()

    if len(text) < size:
        return {text}

    return {
        text[index:index + size]
        for index in range(len(text) - size + 1)
    }


def lexical_similarity(first, second):
    left = character_ngrams(first)
    right = character_ngrams(second)

    if not left or not right:
        return 0.0

    intersection = len(left & right)

    precision = intersection / len(left)
    recall = intersection / len(right)

    if precision + recall == 0:
        return 0.0

    return (
        2 * precision * recall
        / (precision + recall)
    )


# ============================================================
# SEMANTISCHE EMBEDDINGS
# ============================================================

_embedding_model = None
_embedding_attempted = False


def get_embedding_model():
    global _embedding_model
    global _embedding_attempted

    if _embedding_attempted:
        return _embedding_model

    _embedding_attempted = True

    try:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(
            "paraphrase-multilingual-MiniLM-L12-v2"
        )

    except Exception as error:
        print(
            "[MISSIE 9] Embeddings niet beschikbaar:",
            error
        )

        _embedding_model = None

    return _embedding_model


def cosine_similarity(first, second):
    dot = sum(
        a * b
        for a, b in zip(first, second)
    )

    left = math.sqrt(
        sum(a * a for a in first)
    )

    right = math.sqrt(
        sum(b * b for b in second)
    )

    if left == 0 or right == 0:
        return 0.0

    return dot / (left * right)


def calculate_scores(question, fragments):
    """
    Semantisch zoeken wanneer het model
    beschikbaar is.

    Anders terugvallen op lettergroepen.
    """

    model = get_embedding_model()

    if model is None:
        return [
            lexical_similarity(question, fragment)
            for fragment in fragments
        ], "lexical"

    vectors = model.encode(
        [question] + fragments,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32
    )

    question_vector = vectors[0]

    scores = [
        float(
            cosine_similarity(
                question_vector,
                vector
            )
        )
        for vector in vectors[1:]
    ]

    return scores, "semantic"


# ============================================================
# CALLBACKS ZOEKEN
# ============================================================

def find_callbacks(data):
    chat = get_chat(data.chat_id)

    episodes = load_canon(
        data.chat_id,
        data.season,
        data.episode
    )

    candidates = []

    for item in episodes:
        for fragment_number, fragment in enumerate(
            split_fragments(item["text"]),
            start=1
        ):
            candidates.append({
                "season": item["season"],
                "episode": item["episode"],
                "version": item["version"],
                "version_id": item["version_id"],
                "fragment_number": fragment_number,
                "excerpt": fragment
            })

    if not candidates:
        return {
            "chat_id": data.chat_id,
            "au_title": chat["title"],
            "possible_callbacks": [],
            "search_method": None,
            "canon_episodes_searched": len(episodes),
            "message": (
                "Geen eerdere canonfragmenten beschikbaar."
            )
        }

    scores, method = calculate_scores(
        data.content,
        [
            item["excerpt"]
            for item in candidates
        ]
    )

    matches = []

    for item, score in zip(candidates, scores):
        if score < MIN_SIMILARITY:
            continue

        matches.append({
            **item,
            "similarity": round(float(score), 4),
            "relationship": "possible",
            "verified_source": True,
            "verified_connection": False
        })

    matches.sort(
        key=lambda item: item["similarity"],
        reverse=True
    )

    # Maximaal één fragment per oude aflevering
    # voorkomt dat één aflevering alles domineert.
    selected = []
    used_episodes = set()

    for item in matches:
        episode_key = (
            item["season"],
            item["episode"]
        )

        if episode_key in used_episodes:
            continue

        selected.append(item)
        used_episodes.add(episode_key)

        if len(selected) >= data.max_results:
            break

    return {
        "chat_id": data.chat_id,
        "au_title": chat["title"],
        "possible_callbacks": selected,
        "search_method": method,
        "canon_episodes_searched": len(episodes),
        "message": (
            "Mogelijke oude referenties gevonden."
            if selected
            else "Geen voldoende sterke overeenkomst gevonden."
        )
    }


# ============================================================
# ROUTES
# ============================================================

@router.get("/status")
def callback_status():
    return {
        "success": True,
        "module": "Astra Cross-Season Memory",
        "database": str(Path(DB_PATH)),
        "spontaneous_callback_search": True,
        "canon_only": True,
        "future_episode_filter": True,
        "connection_verification": (
            "Mogelijke verbanden, niet automatisch bewezen."
        )
    }


@router.post("/analyze")
def analyze_callbacks(data: CallbackRequest):
    """
    Zoek oude referenties in nieuwe tekst.
    """

    return find_callbacks(data)


@router.post("/chat")
async def callback_chat(
    data: CallbackChatRequest,
    request: Request
):
    """
    Laat de bestaande Astra-engine antwoorden
    en zoek automatisch oude referenties.

    De callback wordt apart teruggegeven.
    Zo kan de Android-app haar spontaan tonen
    zonder de bestaande antwoordregels te omzeilen.
    """

    try:
        import httpx

    except ImportError:
        raise HTTPException(
            status_code=500,
            detail=(
                "Installeer httpx met: pip install httpx"
            )
        )

    callback_request = CallbackRequest(
        chat_id=data.chat_id,
        content=data.content,
        season=data.season,
        episode=data.episode,
        max_results=data.max_results
    )

    # Zoeken buiten de event loop uitvoeren.
    callback_result = await asyncio.to_thread(
        find_callbacks,
        callback_request
    )

    # Gebruik de bestaande Astra-chatroute.
    # Geen tweede, afwijkende AI-engine.
    payload = {
        "chat_id": data.chat_id,
        "mode": data.mode,
        "content": data.content,
        "season": data.season,
        "episode": data.episode
    }

    try:
        transport = httpx.ASGITransport(
            app=request.app
        )

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://astra.internal",
            timeout=300
        ) as client:

            response = await client.post(
                "/astra/chat",
                json=payload
            )

        try:
            original = response.json()

        except ValueError:
            raise HTTPException(
                status_code=502,
                detail="Astra-chat gaf geen JSON terug."
            )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=response.status_code,
                detail=original
            )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Bestaande Astra-chat kon niet "
                f"worden uitgevoerd: {error}"
            )
        )

    return {
        "success": True,
        "chat": original,
        "callbacks": callback_result["possible_callbacks"],
        "callback_message": callback_result["message"],
        "search_method": callback_result["search_method"],
        "canon_episodes_searched": (
            callback_result["canon_episodes_searched"]
        ),
        "important": (
            "Callbacks zijn mogelijke verwijzingen. "
            "De oorspronkelijke canonfragmenten zijn "
            "echt opgeslagen, maar het verband is "
            "niet automatisch bewezen."
        )
    }
