
"""ASTRA AI - Missie 9.7: contextbewuste callbacks (aanvulling op 9.6)."""
import json
import os
import re
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from astra_living_memory import LivingMemoryRequest, analyze_living_memory

router = APIRouter(prefix="/astra/v2/context-callbacks", tags=["Astra Context Callbacks"])

OLLAMA_URL = os.getenv("ASTRA_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.getenv("ASTRA_OLLAMA_MODEL", "qwen2.5:3b")
CATEGORIES = {"nickname", "location", "object", "ambition", "event", "quote", "relationship", "unknown"}


class ContextRequest(LivingMemoryRequest):
    mode: Literal["creative", "canon"] = "creative"


def extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except ValueError:
            return {}


def source_label(source):
    return f"seizoen {source['season']}, aflevering {source['episode']}"


def normalize_candidates(memory):
    """Exacte match = bewijs van tekst; nooit automatisch van betekenis."""
    result = []
    for item in memory.get("callbacks", []):
        source = item.get("source") or {}
        if not all(key in source for key in ("season", "episode", "excerpt")):
            continue
        result.append({
            "type": item.get("type", "unknown"),
            "phrase": item.get("phrase"),
            "source": source,
            "first_recorded": item.get("first_recorded"),
            "last_recorded": item.get("last_recorded"),
            "text_match_verified": item.get("verified") == "exact_text_match",
            "connection_verified": False,
        })
    return result


async def interpret_candidate(content, candidate):
    """Laat Ollama het soort herinnering bepalen, zonder canonfeiten te verzinnen."""
    prompt = {
        "current_scene": content[:3500],
        "matched_phrase": candidate.get("phrase"),
        "old_canon_excerpt": candidate["source"]["excerpt"][:1800],
        "source": {
            "season": candidate["source"]["season"],
            "episode": candidate["source"]["episode"],
        },
    }
    system = (
        "Je beoordeelt mogelijke fanfiction-callbacks. Gebruik uitsluitend de twee aangeleverde "
        "teksten; verzin geen verhaalfeiten. Classificeer de HERINNERING als een van: "
        "nickname, location, object, ambition, event, quote, relationship, unknown. "
        "Een herhaalde woordgroep is NIET automatisch een quote of bijnaam. "
        "Beoordeel of de teksten echt over hetzelfde specifieke ding gaan. "
        "Kies 'uncertain' als dat niet aantoonbaar is. "
        "Geef uitsluitend JSON met keys category, connection (supported|possible|uncertain), "
        "reason, reaction. Reaction: korte enthousiaste Nederlandse fanreactie, "
        "alleen wanneer connection supported of possible, anders lege string. "
        "Bij 'possible' moet de reactie een vraag of twijfel bevatten. "
        "Noem alleen seizoen/aflevering uit de bron. "
        "Noem geen tijdsduur zoals gisteren, vorige week of jaren geleden: "
        "seizoennummers zijn geen kalender. Geen nieuwe personages of gebeurtenissen."
    )
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 250},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        })
        response.raise_for_status()
    raw = extract_json(response.json().get("message", {}).get("content", ""))
    category = raw.get("category", "unknown")
    connection = raw.get("connection", "uncertain")
    if category not in CATEGORIES:
        category = "unknown"
    if connection not in {"supported", "possible", "uncertain"}:
        connection = "uncertain"
    # Het model kan een verband niet zelfstandig bewijzen: hoogstens 'possible'.
    if connection == "supported":
        connection = "possible"
    reaction = str(raw.get("reaction") or "").strip()
    # Modeltekst wordt niet als bewijs gebruikt. Een gecontroleerde reactie
    # vermijdt verzonnen details en temporele aannames.
    if connection == "possible":
        reaction = (
            f"WAIT 😭💗 Doet dit denken aan {source_label(candidate['source'])}?! "
            f"Misschien is dit een {category}-callback!"
        )
    else:
        reaction = ""
    return {
        **candidate,
        "category": category,
        "connection": connection,
        "reason_unverified": str(raw.get("reason") or "")[:300],
        "fan_reaction": reaction,
        "evidence_level": "source_text_only",
    }


async def analyze(data):
    memory = analyze_living_memory(data)
    candidates = normalize_candidates(memory)
    assessed = []
    for candidate in candidates[:data.limit]:
        try:
            assessed.append(await interpret_candidate(data.content, candidate))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            assessed.append({
                **candidate,
                "category": "unknown",
                "connection": "uncertain",
                "reason_unverified": "Classificatie niet beschikbaar",
                "fan_reaction": "",
                "evidence_level": "source_text_only",
            })
    chosen = next((item for item in assessed if item["connection"] == "possible"), None)
    return {
        "success": True,
        "mission": "9.7",
        "chat_id": data.chat_id,
        "current_position": {"season": data.season, "episode": data.episode},
        "canon_episodes_scanned": memory["canon_episodes_scanned"],
        "callbacks": assessed,
        "callback_detected": chosen is not None,
        "fan_reaction": ({
            "text": chosen["fan_reaction"],
            "category": chosen["category"],
            "source": chosen["source"],
            "connection_verified": False,
        } if chosen else None),
        "notice": "Brontekst is controleerbaar; inhoudelijke verbindingen blijven onbewezen. Seizoenen zijn geen tijdseenheden.",
    }


@router.get("/status")
def status():
    return {"success": True, "mission": "9.7", "module": "Context-Aware Callbacks", "chat_integration": True, "canon_guaranteed": False}


@router.post("/analyze")
async def analyze_route(data: ContextRequest):
    return await analyze(data)


@router.post("/chat")
async def chat_route(data: ContextRequest, request: Request):
    result = await analyze(data)
    # De bestaande generator blijft intact. We injecteren geen onbewezen
    # interpretaties in het verhaal en bewaren ze niet als canon.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app),
                                 base_url="http://astra.internal", timeout=300) as client:
        response = await client.post("/astra/chat", json={
            "chat_id": data.chat_id, "season": data.season,
            "episode": data.episode, "content": data.content, "mode": data.mode,
        })
    try:
        chat_result = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Ongeldig antwoord van Astra-chat") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=chat_result)
    return {"success": True, "mission": "9.7", "chat": chat_result,
            "context_callbacks": result, "fan_reaction": result["fan_reaction"]}


def install_astra_context_callbacks(app):
    if not getattr(app.state, "astra_context_callbacks_installed", False):
        app.include_router(router)
        app.state.astra_context_callbacks_installed = True
