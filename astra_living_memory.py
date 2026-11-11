
"""
ASTRA AI - MISSIE 9.6
LIVING MEMORY & SPONTANEOUS REACTIONS

- Herkent terugkerende uitdrukkingen uit eerdere canon
- Zoekt mogelijke verbanden met oude scènes
- Vindt seizoen en aflevering van herinneringen
- Maakt fanreacties met bronvermelding
- Sluit huidige en toekomstige canon uit
- Gebruikt de bestaande Astra-chatgenerator

Geen hardcoded personages, bijnamen of verhaalfacts.
"""

import math
import re
import sqlite3

from collections import Counter, defaultdict
from contextlib import closing

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import DB_PATH


router = APIRouter(
    prefix="/astra/v2/living-memory",
    tags=["Astra Living Memory"],
)

# ============================================
# TECHNISCHE INSTELLINGEN
# ============================================

MAX_CANON_EPISODES = 1000
MAX_TRANSCRIPT_CHARS = 100000
MAX_SCENE_CHARS = 10000

MIN_SENTENCE_SCORE = 0.26
MIN_PHRASE_WORDS = 3
MAX_PHRASE_WORDS = 6


# ============================================
# REQUEST MODELLEN
# ============================================

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


# ============================================
# TEKSTVERWERKING
# ============================================

def words(text):
    return re.findall(
        r"[^\W_]+(?:['’][^\W_]+)*",
        text.casefold(),
        flags=re.UNICODE,
    )


def sentences(text):
    parts = re.split(
        r"(?<=[.!?])\s+|\n+",
        text.strip(),
    )

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


def ngrams(tokens, minimum=3, maximum=6):
    for size in range(minimum, maximum + 1):
        for index in range(len(tokens) - size + 1):
            yield tuple(tokens[index:index + size])


def short_excerpt(text, limit=360):
    text = " ".join(text.split())

    if len(text) <= limit:
        return text

    return text[:limit].rstrip() + "..."


def episode_position(item):
    return (
        item["season"],
        item["episode"],
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
    return db


def load_past_canon(chat_id, season, episode):
    """
    Haal uitsluitend eerdere officiële canon op.

    Huidige en toekomstige afleveringen
    worden uitgesloten.
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

    # Gebruik de nieuwste versie wanneer meerdere
    # canonversies voor een aflevering bestaan.

    unique = {}

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


# ============================================
# WOORDGEWICHTEN
# ============================================

def build_document_frequency(episodes):
    frequency = Counter()

    for episode in episodes:
        frequency.update(
            set(words(episode["transcript"]))
        )

    return frequency


def weighted_similarity(
    current_text,
    old_text,
    document_frequency,
    document_count,
):
    """
    Vergelijk scènes op gedeelde woorden.

    Zeldzamere woorden wegen zwaarder.
    Er wordt geen vaste stopwoordenlijst gebruikt.
    """

    current_tokens = set(words(current_text))
    old_tokens = set(words(old_text))

    if not current_tokens or not old_tokens:
        return 0.0

    shared = current_tokens & old_tokens

    if len(shared) < 2:
        return 0.0

    def weight(token):
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


# ============================================
# EXACTE CALLBACKS
# ============================================

def find_repeated_phrases(
    current_text,
    episodes,
    limit,
):
    """
    Herken woordgroepen die letterlijk
    in oude canon voorkomen.

    Een match bewijst tekstherhaling,
    niet automatisch de betekenis.
    """

    current_tokens = words(current_text)

    if len(current_tokens) < MIN_PHRASE_WORDS:
        return []

    current_phrases = set(
        ngrams(
            current_tokens,
            MIN_PHRASE_WORDS,
            MAX_PHRASE_WORDS,
        )
    )

    if not current_phrases:
        return []

    appearances = defaultdict(list)

    for episode in episodes:
        old_tokens = words(
            episode["transcript"]
        )

        old_phrases = set(
            ngrams(
                old_tokens,
                MIN_PHRASE_WORDS,
                MAX_PHRASE_WORDS,
            )
        )

        shared_phrases = current_phrases & old_phrases

        for phrase in shared_phrases:
            appearances[phrase].append(episode)

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
            "type": "exact_phrase_callback",
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

    # Voorkom overlappende resultaten.

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


# ============================================
# MOGELIJKE SCÈNECALLBACKS
# ============================================

def find_scene_callbacks(
    current_text,
    episodes,
    limit,
):
    """
    Zoek scènes met vergelijkbare inhoud.

    Dit is een lexicale vergelijking.
    Een verband is niet automatisch bewezen.
    """

    if not episodes:
        return []

    frequency = build_document_frequency(episodes)
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
            "type": "possible_scene_callback",
            "score": round(best_score, 4),

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


# ============================================
# FANREACTIES
# ============================================

def build_fan_reaction(callbacks):
    """
    Maak een enthousiaste reactie op basis
    van gevonden canonbronnen.

    Er worden geen extra verhaalfacts verzonnen.
    """

    if not callbacks:
        return None

    exact = [
        item
        for item in callbacks
        if item["type"] == "exact_phrase_callback"
    ]

    if exact:
        best = exact[0]

        first = best["first_recorded"]
        last = best["last_recorded"]

        first_label = (
            f"seizoen {first['season']}, "
            f"aflevering {first['episode']}"
        )

        last_label = (
            f"seizoen {last['season']}, "
            f"aflevering {last['episode']}"
        )

        return {
            "emotion": "surprised",

            "text": (
                "OH MY GOSH 😭💗 "
                "DIE UITSPRAAK KOMT TERUG?! "
                "Ik vind haar al terug in "
                f"{first_label}! "
                "De laatste vermelding die ik "
                "in de doorzochte eerdere canon "
                f"vond, staat in {last_label}. "
                "THE CALLBACK!!"
            ),

            "evidence_type": "exact_text_match",
            "source": best["source"],
        }

    best = callbacks[0]
    source = best["source"]

    return {
        "emotion": "curious",

        "text": (
            "WAITTT 😭💗 "
            "DIT DOET ME DENKEN AAN "
            f"SEIZOEN {source['season']}, "
            f"AFLEVERING {source['episode']}! "
            "Zou dit een callback kunnen zijn?!"
        ),

        "evidence_type": "possible_connection",
        "source": source,
    }


# ============================================
# COMPLETE ANALYSE
# ============================================

def analyze_living_memory(data):
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
            and
            existing["source"]["episode"]
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
        "mission": "9.6",
        "chat_id": data.chat_id,

        "current_position": {
            "season": data.season,
            "episode": data.episode,
        },

        "canon_episodes_scanned": len(episodes),
        "callback_detected": bool(callbacks),
        "callbacks": callbacks,

        "fan_reaction": build_fan_reaction(
            callbacks
        ),

        "limitations": (
            "Exacte tekstherhaling is "
            "controleerbaar. Inhoudelijke "
            "verbanden zijn mogelijke callbacks, "
            "geen bewezen gebeurtenissen. "
            "Eerste en laatste vermeldingen "
            "gelden alleen voor de doorzochte "
            "opgeslagen canon."
        ),
    }


# ============================================
# API STATUS
# ============================================

@router.get("/status")
def living_memory_status():
    return {
        "success": True,
        "mission": "9.6",
        "module": "Astra Living Memory",
        "exact_phrase_callbacks": True,
        "contextual_callback_candidates": True,
        "source_locations": True,
        "fan_reactions": True,
        "future_canon_excluded": True,
        "hardcoded_story_facts": False,
        "chat_integration": True,
        "semantic_understanding_guaranteed": False,
    }


# ============================================
# API ANALYSE
# ============================================

@router.post("/analyze")
def living_memory_analyze(
    data: LivingMemoryRequest,
):
    return analyze_living_memory(data)


# ============================================
# API CHAT + LIVING MEMORY
# ============================================

@router.post("/chat")
async def living_memory_chat(
    data: LivingChatRequest,
    request: Request,
):
    """
    Combineer Living Memory met de
    bestaande Astra-chatgenerator.

    De oorspronkelijke gebruikersboodschap
    wordt ongewijzigd doorgestuurd.

    Fanreactie wordt apart teruggegeven.
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
                "Astra-chat kon niet worden "
                f"bereikt: {exc}"
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
        "mission": "9.6",
        "chat": chat_result,
        "living_memory": memory,
        "fan_reaction": memory["fan_reaction"],
    }


# ============================================
# INSTALLEREN IN FASTAPI
# ============================================

def install_astra_living_memory(app):
    """
    Registreer de routes één keer.
    """

    if getattr(
        app.state,
        "astra_living_memory_installed",
        False,
    ):
        return

    app.include_router(router)

    app.state.astra_living_memory_installed = True
