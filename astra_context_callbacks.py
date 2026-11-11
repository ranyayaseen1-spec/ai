
"""
ASTRA AI - MISSIE 9.7.2
DYNAMISCHE CONTEXTBEWUSTE CALLBACKS

Geen hardcoded:
- Personages
- Verhaalgebeurtenissen
- Bijnamen
- Callbackcategorieën
- Fanreacties

De canon komt uitsluitend uit de database.
Ollama beoordeelt en beschrijft mogelijke verbanden.

Bestaande API-routes blijven behouden.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3

from contextlib import closing
from difflib import SequenceMatcher
from typing import Any, Literal

import httpx

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from database import DB_PATH

from astra_living_memory import (
    LivingMemoryRequest,
    analyze_living_memory,
)


router = APIRouter(
    prefix="/astra/v2/context-callbacks",
    tags=["Astra Context Callbacks"],
)


OLLAMA_URL = os.getenv(
    "ASTRA_OLLAMA_URL",
    "http://127.0.0.1:11434/api/chat",
)

OLLAMA_MODEL = os.getenv(
    "ASTRA_OLLAMA_MODEL",
    "qwen2.5:3b",
)

EMBEDDING_MODEL = os.getenv(
    "ASTRA_EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)

SEMANTIC_THRESHOLD = float(
    os.getenv(
        "ASTRA_CALLBACK_SEMANTIC_THRESHOLD",
        "0.42",
    )
)

LEXICAL_THRESHOLD = float(
    os.getenv(
        "ASTRA_CALLBACK_LEXICAL_THRESHOLD",
        "0.32",
    )
)


_embedding_model = None
_embedding_unavailable = False


class ContextRequest(LivingMemoryRequest):
    mode: Literal["creative", "canon"] = "creative"


def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()

    try:
        value = json.loads(text)

        if isinstance(value, dict):
            return value

    except (ValueError, TypeError):
        pass

    match = re.search(
        r"\{[\s\S]*\}",
        text,
    )

    if match is None:
        return {}

    try:
        value = json.loads(match.group(0))

        return value if isinstance(value, dict) else {}

    except (ValueError, TypeError):
        return {}


def is_prior_source(source, season, episode):
    try:
        position = (
            int(source["season"]),
            int(source["episode"]),
        )

        return position < (season, episode)

    except (KeyError, ValueError, TypeError):
        return False


def normalize_candidates(memory, season, episode):
    result = []

    for item in memory.get("callbacks", []):
        source = item.get("source") or {}

        if not isinstance(source, dict):
            continue

        if not all(
            key in source
            for key in (
                "season",
                "episode",
                "excerpt",
            )
        ):
            continue

        if not is_prior_source(
            source,
            season,
            episode,
        ):
            continue

        result.append({
            "type": item.get("type"),
            "phrase": item.get("phrase"),
            "source": source,
            "first_recorded": item.get(
                "first_recorded"
            ),
            "last_recorded": item.get(
                "last_recorded"
            ),
            "text_match_verified": (
                item.get("verified")
                == "exact_text_match"
            ),
            "connection_verified": False,
            "retrieval_method": "living_memory_9_6",
            "similarity": item.get("score"),
        })

    return result


def load_prior_canon(chat_id, season, episode):
    """
    Alleen eerdere officiële canon uit dezelfde chat.
    """

    with closing(
        sqlite3.connect(
            DB_PATH,
            timeout=30,
        )
    ) as db:

        db.row_factory = sqlite3.Row

        rows = db.execute(
            """
            SELECT
                e.season_id AS season,
                e.episode_number AS episode,
                v.version_id,
                v.transcript_text AS excerpt

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
                e.season_id,
                e.episode_number
            """,
            (
                chat_id,
                season,
                season,
                episode,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def split_into_passages(text, max_chars=850):
    text = (text or "").strip()

    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    passages = []

    step = max_chars - 200

    for start in range(
        0,
        len(text),
        step,
    ):
        passage = text[
            start:start + max_chars
        ].strip()

        if passage:
            passages.append(passage)

        if start + max_chars >= len(text):
            break

    return passages


def lexical_similarity(a, b):
    a = re.sub(
        r"\s+",
        " ",
        a.casefold(),
    ).strip()

    b = re.sub(
        r"\s+",
        " ",
        b.casefold(),
    ).strip()

    if not a or not b:
        return 0.0

    sentences_a = [
        item.strip()
        for item in re.split(
            r"[.!?\n]+",
            a,
        )
        if len(item.strip()) >= 12
    ]

    sentences_b = [
        item.strip()
        for item in re.split(
            r"[.!?\n]+",
            b,
        )
        if len(item.strip()) >= 12
    ]

    if not sentences_a:
        sentences_a = [a]

    if not sentences_b:
        sentences_b = [b]

    best = 0.0

    for current in sentences_a[:30]:
        for previous in sentences_b[:30]:

            score = SequenceMatcher(
                None,
                current,
                previous,
                autojunk=False,
            ).ratio()

            best = max(
                best,
                score,
            )

    return best


def get_embedding_model():
    global _embedding_model
    global _embedding_unavailable

    if _embedding_unavailable:
        return None

    if _embedding_model is not None:
        return _embedding_model

    try:
        from sentence_transformers import (
            SentenceTransformer,
        )

        _embedding_model = SentenceTransformer(
            EMBEDDING_MODEL
        )

        return _embedding_model

    except (
        ImportError,
        OSError,
        RuntimeError,
    ):
        _embedding_unavailable = True
        return None


def semantic_scores(question, passages):
    model = get_embedding_model()

    if model is None or not passages:
        return None

    try:
        embeddings = model.encode(
            [question] + passages,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        query_vector = embeddings[0]

        return [
            float(query_vector @ vector)
            for vector in embeddings[1:]
        ]

    except (
        ValueError,
        RuntimeError,
        TypeError,
    ):
        return None


def retrieve_canon_candidates(
    content,
    rows,
    limit,
):
    passages = []
    sources = []

    for row in rows:

        for passage in split_into_passages(
            row["excerpt"]
        ):

            passages.append(passage)

            sources.append({
                "season": row["season"],
                "episode": row["episode"],
                "version_id": row["version_id"],
                "excerpt": passage,
            })

    if not passages:
        return []

    scores = semantic_scores(
        content,
        passages,
    )

    candidates = []

    for index, passage in enumerate(passages):

        lexical = lexical_similarity(
            content,
            passage,
        )

        if scores is not None:
            semantic = scores[index]

            matched = (
                semantic >= SEMANTIC_THRESHOLD
                or lexical >= LEXICAL_THRESHOLD
            )

            similarity = max(
                semantic,
                lexical,
            )

            method = (
                "semantic"
                if semantic >= lexical
                else "lexical"
            )

        else:
            matched = (
                lexical >= LEXICAL_THRESHOLD
            )

            similarity = lexical
            method = "lexical"

        if not matched:
            continue

        candidates.append({
            "type": None,
            "phrase": None,
            "source": sources[index],
            "first_recorded": None,
            "last_recorded": None,
            "text_match_verified": False,
            "connection_verified": False,
            "retrieval_method": method,
            "similarity": round(
                similarity,
                4,
            ),
        })

    candidates.sort(
        key=lambda item: item["similarity"],
        reverse=True,
    )

    selected = []
    seen = set()

    for candidate in candidates:
        source = candidate["source"]

        key = (
            source["season"],
            source["episode"],
        )

        if key in seen:
            continue

        seen.add(key)
        selected.append(candidate)

        if len(selected) >= limit:
            break

    return selected


async def interpret_candidate(content, candidate):
    """
    Ollama beschrijft zelfstandig wat terugkomt.

    Geen vaste categorieën.
    Geen vooraf geschreven fanreacties.
    """

    source = candidate["source"]

    payload = {
        "current_scene": content[:3500],
        "previous_canon": {
            "season": source["season"],
            "episode": source["episode"],
            "excerpt": source["excerpt"][:1800],
        },
    }

    system = (
        "Je onderzoekt mogelijke verwijzingen "
        "tussen een actuele fictiescène en eerdere canon. "
        "Gebruik uitsluitend de aangeleverde teksten. "
        "Beschrijf in je eigen woorden wat mogelijk terugkomt. "
        "Gebruik geen vooraf bepaalde categorieën. "
        "Bedenk geen ontbrekende gebeurtenissen, "
        "citaten, personages of tijdsverlopen. "
        "Een gedeeld woord is niet automatisch "
        "een betekenisvolle verwijzing. "
        "Als het verband onduidelijk is, "
        "geef dan aan dat het onzeker is. "
        "Als er een betekenisvol mogelijk verband is, "
        "mag je een korte, spontane fanreactie schrijven. "
        "De reactie moet passen bij de specifieke scène "
        "en mag geen standaardzinnetje zijn. "
        "Gebruik seizoen- en afleveringsnummers "
        "uitsluitend uit de brongegevens. "
        "Behandel seizoenen niet als kalenderperiodes. "
        "Geef uitsluitend een JSON-object met: "
        "connection (possible of uncertain), "
        "memory_description (string), "
        "reason (string), "
        "fan_reaction (string). "
        "Bij onzekerheid moet fan_reaction leeg zijn."
    )

    async with httpx.AsyncClient(
        timeout=120,
    ) as client:

        response = await client.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.3,
                    "num_predict": 350,
                },
                "messages": [
                    {
                        "role": "system",
                        "content": system,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
        )

        response.raise_for_status()

    raw = extract_json(
        response.json()
        .get("message", {})
        .get("content", "")
    )

    connection = raw.get(
        "connection",
        "uncertain",
    )

    if connection != "possible":
        connection = "uncertain"

    description = str(
        raw.get("memory_description") or ""
    )[:500]

    reason = str(
        raw.get("reason") or ""
    )[:500]

    reaction = str(
        raw.get("fan_reaction") or ""
    )[:1000]

    if connection == "uncertain":
        reaction = ""

    return {
        **candidate,
        "memory_description": description,
        "connection": connection,
        "reason_unverified": reason,
        "fan_reaction": reaction,
        "evidence_level": "source_text_only",
    }


async def analyze(data):
    memory = analyze_living_memory(data)

    candidates = normalize_candidates(
        memory,
        data.season,
        data.episode,
    )

    rows = load_prior_canon(
        data.chat_id,
        data.season,
        data.episode,
    )

    fallback = retrieve_canon_candidates(
        data.content,
        rows,
        data.limit,
    )

    combined = []
    seen = set()

    for candidate in candidates + fallback:

        source = candidate["source"]

        key = (
            source["season"],
            source["episode"],
        )

        if key in seen:
            continue

        seen.add(key)
        combined.append(candidate)

        if len(combined) >= data.limit:
            break

    assessed = []

    for candidate in combined:

        try:
            assessed.append(
                await interpret_candidate(
                    data.content,
                    candidate,
                )
            )

        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
        ):
            assessed.append({
                **candidate,
                "memory_description": "",
                "connection": "uncertain",
                "reason_unverified": (
                    "AI-beoordeling niet beschikbaar"
                ),
                "fan_reaction": "",
                "evidence_level": "source_text_only",
            })

    chosen = next(
        (
            item
            for item in assessed
            if item["connection"] == "possible"
        ),
        None,
    )

    return {
        "success": True,
        "mission": "9.7.2",
        "chat_id": data.chat_id,
        "current_position": {
            "season": data.season,
            "episode": data.episode,
        },
        "canon_episodes_scanned": len(rows),
        "retrieval_candidates": len(combined),
        "callbacks": assessed,
        "callback_detected": chosen is not None,
        "fan_reaction": (
            {
                "text": chosen["fan_reaction"],
                "memory_description": chosen[
                    "memory_description"
                ],
                "source": chosen["source"],
                "connection_verified": False,
            }
            if chosen
            else None
        ),
        "notice": (
            "De canonbron bestaat. "
            "De inhoudelijke interpretatie "
            "is een AI-beoordeling en geen "
            "automatisch bewezen verband."
        ),
    }


@router.get("/status")
def status():
    return {
        "success": True,
        "mission": "9.7.2",
        "module": "Dynamic Context Callbacks",
        "dynamic_memory_descriptions": True,
        "dynamic_fan_reactions": True,
        "fixed_callback_categories": False,
        "hardcoded_story_facts": False,
        "canon_guaranteed": False,
    }


@router.post("/analyze")
async def analyze_route(data: ContextRequest):
    return await analyze(data)


@router.post("/chat")
async def chat_route(
    data: ContextRequest,
    request: Request,
):
    result = await analyze(data)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=request.app
        ),
        base_url="http://astra.internal",
        timeout=300,
    ) as client:

        response = await client.post(
            "/astra/chat",
            json={
                "chat_id": data.chat_id,
                "season": data.season,
                "episode": data.episode,
                "content": data.content,
                "mode": data.mode,
            },
        )

    try:
        chat_result = response.json()

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Ongeldig antwoord van Astra-chat",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=chat_result,
        )

    return {
        "success": True,
        "mission": "9.7.2",
        "chat": chat_result,
        "context_callbacks": result,
        "fan_reaction": result["fan_reaction"],
    }


def install_astra_context_callbacks(app):
    if not getattr(
        app.state,
        "astra_context_callbacks_installed",
        False,
    ):
        app.include_router(router)

        app.state.astra_context_callbacks_installed = True
