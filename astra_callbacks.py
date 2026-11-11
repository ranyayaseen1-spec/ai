
"""
ASTRA AI - CROSS-SEASON CALLBACKS
MISSIE 9.4

Routes:
GET  /astra/callbacks/status
POST /astra/callbacks/analyze
POST /astra/callbacks/chat

Deze module:
- zoekt eerdere officiele canon
- vindt mogelijke semantische verbanden
- houdt de originele gebruikersvraag intact
- laat de Canon Engine de tekst genereren
- rapporteert de nieuwe kwaliteitscontroles

Geen hardcoded personages, verhaalfeiten,
stopwoorden of verboden verhaalzinnen.
"""

import asyncio
import math
import re
import sqlite3

from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import httpx

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from pydantic import BaseModel, Field

from database import DB_PATH


# ============================================
# INSTELLINGEN
# ============================================

MIN_SIMILARITY = 0.42
MAX_FRAGMENT_CHARS = 1200
DEFAULT_MAX_RESULTS = 5

EMBEDDING_MODEL = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)

router = APIRouter(
    prefix="/astra/callbacks",
    tags=["Astra - Cross-season memory"],
)


# ============================================
# REQUESTMODELLEN
# ============================================

class CallbackRequest(BaseModel):
    chat_id: int = Field(gt=0)
    content: str = Field(min_length=1)

    season: Optional[int] = Field(
        default=None,
        ge=1,
    )

    episode: Optional[int] = Field(
        default=None,
        ge=1,
    )

    max_results: int = Field(
        default=DEFAULT_MAX_RESULTS,
        ge=1,
        le=20,
    )


class CallbackChatRequest(CallbackRequest):
    mode: Literal[
        "canon",
        "creative",
    ] = "creative"


# ============================================
# DATABASE
# ============================================

def connect():
    db = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    db.row_factory = sqlite3.Row

    db.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return db


def get_chat(chat_id):
    with closing(connect()) as db:
        row = db.execute(
            """
            SELECT chat_id, title
            FROM conversations
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="AU-chat niet gevonden.",
        )

    return dict(row)


def load_canon(
    chat_id,
    season=None,
    episode=None,
):
    """
    Zoek uitsluitend officiele canon.

    Als de huidige tijdlijn bekend is,
    sluit toekomstige en huidige
    afleveringen uit.
    """

    sql = """
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

    params = [chat_id]

    if season is not None:
        if episode is None:
            sql += """
                AND e.season_id < ?
            """
            params.append(season)
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

            params.extend([
                season,
                season,
                episode,
            ])

    sql += """
        ORDER BY
            e.season_id,
            e.episode_number,
            v.version_id
    """

    with closing(connect()) as db:
        rows = db.execute(
            sql,
            params,
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================
# TEKSTFRAGMENTEN
# ============================================

def split_fragments(text):
    text = str(text or "").strip()

    if not text:
        return []

    paragraphs = re.split(
        r"\n\s*\n+",
        text,
    )

    fragments = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        if len(paragraph) <= MAX_FRAGMENT_CHARS:
            fragments.append(paragraph)
            continue

        step = MAX_FRAGMENT_CHARS - 200

        for start in range(
            0,
            len(paragraph),
            step,
        ):
            fragment = paragraph[
                start:start + MAX_FRAGMENT_CHARS
            ].strip()

            if fragment:
                fragments.append(fragment)

            if (
                start + MAX_FRAGMENT_CHARS
                >= len(paragraph)
            ):
                break

    return fragments


# ============================================
# LEXICALE VERGELIJKING
# ============================================

def normalize(text):
    return " ".join(
        str(text).casefold().split()
    )


def character_ngrams(text, size=3):
    text = normalize(text)

    if not text:
        return set()

    if len(text) < size:
        return {text}

    return {
        text[index:index + size]
        for index in range(
            len(text) - size + 1
        )
    }


def lexical_similarity(first, second):
    left = character_ngrams(first)
    right = character_ngrams(second)

    if not left or not right:
        return 0.0

    intersection = len(
        left & right
    )

    precision = (
        intersection / len(left)
    )

    recall = (
        intersection / len(right)
    )

    if precision + recall == 0:
        return 0.0

    return (
        2 * precision * recall
        / (precision + recall)
    )


# ============================================
# SEMANTISCH ZOEKEN
# ============================================

@lru_cache(maxsize=1)
def get_embedding_model():
    from sentence_transformers import (
        SentenceTransformer,
    )

    return SentenceTransformer(
        EMBEDDING_MODEL
    )


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
    try:
        model = get_embedding_model()

        vectors = model.encode(
            [question] + fragments,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )

        query_vector = vectors[0]

        scores = [
            float(
                cosine_similarity(
                    query_vector,
                    vector,
                )
            )
            for vector in vectors[1:]
        ]

        return scores, "semantic"

    except Exception as error:
        print(
            "[ASTRA CALLBACKS] "
            "Semantisch zoeken niet beschikbaar:",
            error,
        )

        scores = [
            lexical_similarity(
                question,
                fragment,
            )
            for fragment in fragments
        ]

        return scores, "lexical"


# ============================================
# CALLBACKS VINDEN
# ============================================

def find_callbacks(data):
    chat = get_chat(
        data.chat_id
    )

    episodes = load_canon(
        data.chat_id,
        data.season,
        data.episode,
    )

    candidates = []

    for episode in episodes:
        fragments = split_fragments(
            episode["text"]
        )

        for number, fragment in enumerate(
            fragments,
            start=1,
        ):
            candidates.append({
                "season": episode["season"],
                "episode": episode["episode"],
                "version": episode["version"],
                "version_id": episode["version_id"],
                "fragment_number": number,
                "excerpt": fragment,
            })

    if not candidates:
        return {
            "chat_id": data.chat_id,
            "au_title": chat["title"],
            "possible_callbacks": [],
            "search_method": None,
            "canon_episodes_searched": len(episodes),
            "message": (
                "Geen eerdere canonfragmenten "
                "gevonden."
            ),
        }

    scores, method = calculate_scores(
        data.content,
        [
            item["excerpt"]
            for item in candidates
        ],
    )

    matches = []

    for candidate, score in zip(
        candidates,
        scores,
    ):
        if score < MIN_SIMILARITY:
            continue

        matches.append({
            **candidate,
            "similarity": round(
                float(score),
                4,
            ),
            "relationship": "possible",
            "verified_source": True,
            "verified_connection": False,
        })

    matches.sort(
        key=lambda item: item["similarity"],
        reverse=True,
    )

    selected = []
    used_episodes = set()

    for match in matches:
        episode_key = (
            match["season"],
            match["episode"],
        )

        if episode_key in used_episodes:
            continue

        selected.append(match)
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
            "Mogelijke eerdere "
            "canonverbanden gevonden."
            if selected
            else
            "Geen sterke overeenkomst gevonden."
        ),
    }


# ============================================
# BESTAANDE ASTRA CHAT AANROEPEN
# ============================================

async def call_existing_astra_chat(
    request,
    chat_id,
    mode,
    content,
    season,
    episode,
):
    """
    De originele tekst blijft intact.

    De Canon Engine haalt zelfstandig
    relevante canon op en controleert
    het gegenereerde antwoord.
    """

    payload = {
        "chat_id": chat_id,
        "mode": mode,
        "content": content,
        "season": season,
        "episode": episode,
    }

    transport = httpx.ASGITransport(
        app=request.app,
    )

    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://astra.internal",
            timeout=600,
        ) as client:
            response = await client.post(
                "/astra/chat",
                json=payload,
            )

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "De interne Astra-chat "
                "kon niet worden uitgevoerd: "
                f"{error}"
            ),
        ) from error

    try:
        result = response.json()

    except ValueError as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "De interne Astra-chat gaf "
                "geen geldig JSON-antwoord."
            ),
        ) from error

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=result.get(
                "detail",
                result,
            ),
        )

    return result


# ============================================
# STATUS
# ============================================

@router.get("/status")
def callback_status():
    return {
        "success": True,
        "module": "Astra Cross-Season Memory",
        "version": "9.4",
        "database": str(Path(DB_PATH)),
        "spontaneous_callback_search": True,
        "canon_context_before_generation": True,
        "existing_canon_engine_used": True,
        "canon_only": True,
        "future_episode_filter": True,
        "original_user_message_preserved": True,
        "clean_creative_history": True,
        "repetition_detection": True,
        "ai_canon_review": True,
        "connection_verification": (
            "Mogelijke verbanden zijn niet "
            "automatisch bewezen."
        ),
    }


# ============================================
# ANALYSE
# ============================================

@router.post("/analyze")
async def analyze_callbacks(
    data: CallbackRequest,
):
    return await asyncio.to_thread(
        find_callbacks,
        data,
    )


# ============================================
# SPONTANE CALLBACK CHAT
# ============================================

@router.post("/chat")
async def callback_chat(
    data: CallbackChatRequest,
    request: Request,
):
    search_request = CallbackRequest(
        chat_id=data.chat_id,
        content=data.content,
        season=data.season,
        episode=data.episode,
        max_results=data.max_results,
    )

    callback_result = await asyncio.to_thread(
        find_callbacks,
        search_request,
    )

    callbacks = callback_result[
        "possible_callbacks"
    ]

    # Geen extra instructies in de user-prompt.
    # De Canon Engine verzorgt zelf:
    # - canoncontext
    # - verboden-zinnencontrole
    # - herhalingscontrole
    # - AI-canonreview

    original_chat = await call_existing_astra_chat(
        request=request,
        chat_id=data.chat_id,
        mode=data.mode,
        content=data.content,
        season=data.season,
        episode=data.episode,
    )

    return {
        "success": True,
        "chat": original_chat,
        "callbacks": callbacks,
        "callback_message": (
            callback_result["message"]
        ),
        "search_method": (
            callback_result["search_method"]
        ),
        "canon_episodes_searched": (
            callback_result[
                "canon_episodes_searched"
            ]
        ),
        "canon_context_supplied": bool(
            original_chat.get("sources")
        ),
        "automatic_callback_generation": (
            data.mode == "creative"
        ),
        "clean_memory": original_chat.get(
            "clean_memory",
            False,
        ),
        "repetition_check_passed": (
            original_chat.get(
                "repetition_check_passed"
            )
        ),
        "canon_review_checked": (
            original_chat.get(
                "canon_review_checked"
            )
        ),
        "canon_review_flagged": (
            original_chat.get(
                "canon_review_flagged"
            )
        ),
        "canon_verified": False,
        "connection_verified": False,
        "important": (
            "De gevonden fragmenten komen "
            "uit officiele canon. "
            "Semantische overeenkomsten en "
            "AI-beoordelingen zijn geen "
            "garantie op canoncorrectheid."
        ),
    }
