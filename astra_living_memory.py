
"""
ASTRA AI - MISSIE 9.6.1
LIVING MEMORY - DYNAMISCHE GEHEUGENBRONNEN

Deze module:
- Leest eerdere officiële canon uit SQLite.
- Herkent letterlijk herhaalde woordgroepen.
- Zoekt lexicaal vergelijkbare scènes.
- Geeft controleerbare bronlocaties terug.
- Sluit huidige en toekomstige afleveringen uit.
- Gebruikt de bestaande Astra-chatgenerator.

Deze module bevat GEEN:
- Vooraf geschreven fanreacties.
- Vaste emotielabels.
- Verhaalgebonden callbackcategorieën.
- Hardcoded personages, bijnamen of gebeurtenissen.

Dynamische interpretatie en fanreacties horen bij
de bovenliggende AI-modules, zoals missie 9.7.2.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3

from collections import Counter, defaultdict
from contextlib import closing
from typing import Any

import httpx

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import DB_PATH


router = APIRouter(
    prefix="/astra/v2/living-memory",
    tags=["Astra Living Memory"],
)


# Technische instellingen, geen verhaalinhoud.

MAX_CANON_EPISODES = int(
    os.getenv("ASTRA_MAX_CANON_EPISODES", "1000")
)

MAX_TRANSCRIPT_CHARS = int(
    os.getenv("ASTRA_MAX_TRANSCRIPT_CHARS", "100000")
)

MAX_SCENE_CHARS = int(
    os.getenv("ASTRA_MAX_SCENE_CHARS", "10000")
)

MIN_SENTENCE_SCORE = float(
    os.getenv("ASTRA_MEMORY_SENTENCE_SCORE", "0.26")
)

MIN_PHRASE_WORDS = int(
    os.getenv("ASTRA_MEMORY_MIN_PHRASE_WORDS", "3")
)

MAX_PHRASE_WORDS = int(
    os.getenv("ASTRA_MEMORY_MAX_PHRASE_WORDS", "6")
)


class LivingMemoryRequest(BaseModel):
    chat_id: int = Field(gt=0)
    season: int = Field(ge=1)
    episode: int = Field(ge=1)

    content: str = Field(
        min_length=1,
        max_length=MAX_SCENE_CHARS,
    )

    limit: int = Field(
        default=5,
        ge=1,
        le=10,
    )


class LivingChatRequest(LivingMemoryRequest):
    mode: str = Field(
        default="creative",
        pattern="^(creative|canon)$",
    )


def words(text: str) -> list[str]:
    return re.findall(
        r"[^\W_]+(?:['’][^\W_]+)*",
        text.casefold(),
        flags=re.UNICODE,
    )


def sentences(text: str) -> list[str]:
    parts = re.split(
        r"(?<=[.!?])\s+|\n+",
        text.strip(),
    )

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


def ngrams(
    tokens: list[str],
    minimum: int = MIN_PHRASE_WORDS,
    maximum: int = MAX_PHRASE_WORDS,
):
    for size in range(minimum, maximum + 1):
        for index in range(len(tokens) - size + 1):
            yield tuple(
                tokens[index:index + size]
            )


def short_excerpt(
    text: str,
    limit: int = 360,
) -> str:
    text = " ".join(text.split())

    if len(text) <= limit:
        return text

    return text[:limit].rstrip() + "..."


def episode_position(
    item: dict[str, Any],
) -> tuple[int, int]:
    return (
        int(item["season"]),
        int(item["episode"]),
    )


def db_open() -> sqlite3.Connection:
    db = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    db.row_factory = sqlite3.Row

    return db


def load_past_canon(
    chat_id: int,
    season: int,
    episode: int,
) -> list[dict[str, Any]]:
    """
    Haal alleen eerdere officiële canon op
    uit dezelfde chat.
    """

    with closing(db_open()) as db:
        chat = db.execute(
            """
            SELECT 1
            FROM conversations
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()

        if chat is None:
            raise HTTPException(
                status_code=404,
                detail="Chat niet gevonden",
            )

        rows = db.execute(
            """
            SELECT
                e.episode_id,
                e.season_id AS season,
                e.episode_number AS episode,
                v.version_id,
                v.transcript_text AS transcript

            FROM episodes e

            JOIN episode_versions v
                ON v.episode_id = e.episode_id

            WHERE e.chat_id = ?
              AND v.is_canon = 1
              AND v.transcript_text IS NOT NULL
              AND TRIM(v.transcript_text) != ''

              AND (
                    e.season_id < ?
                    OR (
                        e.season_id = ?
                        AND e.episode_number < ?
                    )
              )

            ORDER BY
                e.season_id DESC,
                e.episode_number DESC,
                v.version_id DESC

            LIMIT ?
            """,
            (
                chat_id,
                season,
                season,
                episode,
                MAX_CANON_EPISODES,
            ),
        ).fetchall()

    # Behoud de nieuwste officiële canonversie
    # wanneer meerdere versies aanwezig zijn.

    unique: dict[int, dict[str, Any]] = {}

    for row in rows:
        episode_id = row["episode_id"]

        if episode_id in unique:
            continue

        unique[episode_id] = {
            "episode_id": episode_id,
            "season": row["season"],
            "episode": row["episode"],
            "version_id": row["version_id"],
            "transcript": row["transcript"][
                :MAX_TRANSCRIPT_CHARS
            ],
        }

    return sorted(
        unique.values(),
        key=episode_position,
    )


def build_document_frequency(
    episodes: list[dict[str, Any]],
) -> Counter:
    frequency = Counter()

    for episode in episodes:
        frequency.update(
            set(words(episode["transcript"]))
        )

    return frequency


def weighted_similarity(
    current_text: str,
    old_text: str,
    document_frequency: Counter,
    document_count: int,
) -> float:
    """
    Vergelijk gedeelde woorden met gewichten
    op basis van de gevonden canon.

    Geen vaste verhaalwoorden of stopwoordenlijst.
    """

    current_tokens = set(words(current_text))
    old_tokens = set(words(old_text))

    if not current_tokens or not old_tokens:
        return 0.0

    shared = current_tokens & old_tokens

    if len(shared) < 2:
        return 0.0

    def weight(token: str) -> float:
        return math.log(
            (document_count + 1)
            / (document_frequency.get(token, 0) + 1)
        ) + 1.0

    shared_weight = sum(
        weight(token) ** 2
        for token in shared
    )

    current_weight = sum(
        weight(token) ** 2
        for token in current_tokens
    )

    old_weight = sum(
        weight(token) ** 2
        for token in old_tokens
    )

    denominator = math.sqrt(
        current_weight * old_weight
    )

    if denominator == 0:
        return 0.0

    return shared_weight / denominator


def find_repeated_phrases(
    current_text: str,
    episodes: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """
    Zoek letterlijk herhaalde woordgroepen.

    Een exacte match bewijst tekstherhaling,
    maar niet automatisch een verhaalverband.
    """

    current_tokens = words(current_text)

    if len(current_tokens) < MIN_PHRASE_WORDS:
        return []

    current_phrases = set(
        ngrams(current_tokens)
    )

    if not current_phrases:
        return []

    appearances = defaultdict(list)

    for episode in episodes:
        old_tokens = words(
            episode["transcript"]
        )

        old_phrases = set(
            ngrams(old_tokens)
        )

        shared_phrases = (
            current_phrases & old_phrases
        )

        for phrase in shared_phrases:
            appearances[phrase].append(
                episode
            )

    results = []

    for phrase_tokens, found in appearances.items():
        first = min(
            found,
            key=episode_position,
        )

        last = max(
            found,
            key=episode_position,
        )

        phrase = " ".join(phrase_tokens)

        score = (
            len(phrase_tokens) * 2
            + 1 / len(found)
        )

        results.append({
            # Technische matchinformatie,
            # geen verhaalgebonden categorie.
            "type": None,
            "phrase": phrase,
            "score": round(score, 4),

            "first_recorded": {
                "season": first["season"],
                "episode": first["episode"],
            },

            "last_recorded": {
                "season": last["season"],
                "episode": last["episode"],
            },

            "recorded_episodes": len(found),

            "source": {
                "season": last["season"],
                "episode": last["episode"],
                "version_id": last["version_id"],
                "excerpt": short_excerpt(
                    last["transcript"]
                ),
            },

            "verified": "exact_text_match",
        })

    results.sort(
        key=lambda item: (
            item["score"],
            -item["recorded_episodes"],
        ),
        reverse=True,
    )

    selected = []

    for item in results:
        phrase = item["phrase"]

        if any(
            phrase in previous["phrase"]
            for previous in selected
        ):
            continue

        selected.append(item)

        if len(selected) >= limit:
            break

    return selected


def find_scene_callbacks(
    current_text: str,
    episodes: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """
    Zoek lexicaal vergelijkbare scènes.

    De score is een zoeksignaal,
    geen bewijs van een verhaalverband.
    """

    if not episodes:
        return []

    frequency = build_document_frequency(
        episodes
    )

    document_count = len(episodes)

    current_sentences = (
        sentences(current_text)
        or [current_text]
    )

    results = []

    for episode in episodes:
        old_sentences = (
            sentences(episode["transcript"])
            or [episode["transcript"]]
        )

        best_score = 0.0
        best_excerpt = ""

        for current in current_sentences:
            for old in old_sentences:
                score = weighted_similarity(
                    current,
                    old,
                    frequency,
                    document_count,
                )

                if score > best_score:
                    best_score = score
                    best_excerpt = old

        if best_score < MIN_SENTENCE_SCORE:
            continue

        results.append({
            "type": None,
            "phrase": None,
            "score": round(
                best_score,
                4,
            ),

            "source": {
                "season": episode["season"],
                "episode": episode["episode"],
                "version_id": episode["version_id"],
                "excerpt": short_excerpt(
                    best_excerpt
                ),
            },

            "verified": (
                "source_exists_connection_unverified"
            ),
        })

    results.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return results[:limit]


def analyze_living_memory(
    data: LivingMemoryRequest,
) -> dict[str, Any]:
    """
    Geef alleen geheugenresultaten terug.

    De module formuleert zelf geen fanreacties.
    """

    episodes = load_past_canon(
        data.chat_id,
        data.season,
        data.episode,
    )

    exact = find_repeated_phrases(
        data.content,
        episodes,
        data.limit,
    )

    contextual = find_scene_callbacks(
        data.content,
        episodes,
        data.limit,
    )

    callbacks = list(exact)

    for item in contextual:
        same_source = any(
            existing["source"]["season"]
            == item["source"]["season"]
            and existing["source"]["episode"]
            == item["source"]["episode"]
            for existing in callbacks
        )

        if not same_source:
            callbacks.append(item)

        if len(callbacks) >= data.limit:
            break

    callbacks = callbacks[:data.limit]

    return {
        "success": True,
        "mission": "9.6.1",
        "chat_id": data.chat_id,

        "current_position": {
            "season": data.season,
            "episode": data.episode,
        },

        "canon_episodes_scanned": len(episodes),
        "callback_detected": bool(callbacks),
        "callbacks": callbacks,

        # Behouden voor compatibiliteit met
        # bestaande API-gebruikers.
        # Geen vaste fanreactie meer.
        "fan_reaction": None,

        "limitations": (
            "Geheugenresultaten zijn zoekkandidaten. "
            "Exacte tekstherhaling is controleerbaar, "
            "maar betekenisvolle verhaalverbanden "
            "zijn hiermee niet bewezen. "
            "Eerste en laatste vermeldingen gelden "
            "alleen voor de doorzochte canon."
        ),
    }


@router.get("/status")
def living_memory_status():
    return {
        "success": True,
        "mission": "9.6.1",
        "module": "Astra Living Memory",

        "exact_phrase_callbacks": True,
        "contextual_callback_candidates": True,
        "source_locations": True,

        "fan_reactions": False,
        "hardcoded_reaction_templates": False,
        "hardcoded_emotion_labels": False,
        "fixed_story_categories": False,

        "future_canon_excluded": True,
        "hardcoded_story_facts": False,
        "chat_integration": True,
        "semantic_understanding_guaranteed": False,
    }


@router.post("/analyze")
def living_memory_analyze(
    data: LivingMemoryRequest,
):
    return analyze_living_memory(data)


@router.post("/chat")
async def living_memory_chat(
    data: LivingChatRequest,
    request: Request,
):
    """
    Combineer geheugenresultaten met
    de bestaande Astra-chatgenerator.

    De oorspronkelijke boodschap blijft
    ongewijzigd.

    Fanreacties worden hier niet gegenereerd.
    """

    memory = analyze_living_memory(data)

    payload = {
        "chat_id": data.chat_id,
        "season": data.season,
        "episode": data.episode,
        "mode": data.mode,
        "content": data.content,
    }

    transport = httpx.ASGITransport(
        app=request.app,
    )

    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://astra.internal",
            timeout=300,
        ) as client:
            response = await client.post(
                "/astra/chat",
                json=payload,
            )

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Astra-chat kon niet worden bereikt: "
                f"{type(exc).__name__}"
            ),
        ) from exc

    try:
        chat_result = response.json()

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Ongeldig JSON-antwoord "
                "van Astra-chat"
            ),
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=chat_result,
        )

    return {
        "success": True,
        "mission": "9.6.1",
        "chat": chat_result,
        "living_memory": memory,
        "fan_reaction": None,
    }


def install_astra_living_memory(app):
    """
    Registreer de bestaande API-routes
    zonder dubbele registratie.
    """

    if getattr(
        app.state,
        "astra_living_memory_installed",
        False,
    ):
        return

    app.include_router(router)

    app.state.astra_living_memory_installed = True
