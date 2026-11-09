
"""
ASTRA AI - MISSIE 7
VOLLEDIGE DOCX-IMPORTTEST

Deze test:
1. Controleert of de Astra API bereikbaar is.
2. Maakt automatisch een DOCX-testverhaal.
3. Importeert het document via /imports/file.
4. Controleert de importrespons.
5. Controleert de opgeslagen tekst via /versions/{id}.
6. Controleert of de nieuwe versie GEEN canon is.
7. Controleert of bestaande canon behouden blijft.
8. Ruimt het tijdelijke DOCX-bestand op.

Geen vaste personages, verhaalgebeurtenissen
of verboden-zinnenlijsten.

Starten:
    python ai/test_missie7_docx.py

Vereist:
    pip install python-docx
"""

import json
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

from pathlib import Path

from docx import Document


# ============================================
# INSTELLINGEN
# ============================================

API_URL = "http://127.0.0.1:8001"

CHAT_ID = 1
SEASON = 99
EPISODE = 1

REQUEST_TIMEOUT = 180


# ============================================
# HTTP
# ============================================

def api_get(path):

    request = urllib.request.Request(
        API_URL + path,
        method="GET",
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def multipart_upload(
    path,
    fields,
    file_path,
):

    boundary = (
        "astra-boundary-"
        + uuid.uuid4().hex
    )

    body = bytearray()

    for name, value in fields.items():

        body.extend(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; '
                f'name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    filename = file_path.name

    body.extend(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; '
            f'name="file"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: "
            f"application/vnd.openxmlformats-"
            f"officedocument.wordprocessingml."
            f"document\r\n\r\n"
        ).encode("utf-8")
    )

    body.extend(
        file_path.read_bytes()
    )

    body.extend(b"\r\n")

    body.extend(
        f"--{boundary}--\r\n".encode(
            "utf-8"
        )
    )

    request = urllib.request.Request(
        API_URL + path,
        data=bytes(body),
        headers={
            "Content-Type": (
                "multipart/form-data; "
                f"boundary={boundary}"
            )
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:

        return (
            response.status,
            json.loads(
                response.read().decode(
                    "utf-8"
                )
            ),
        )


# ============================================
# TESTDOCUMENT MAKEN
# ============================================

def make_test_document(path, token):

    document = Document()

    document.add_heading(
        "Astra Import Test",
        level=1,
    )

    document.add_paragraph(
        "Dit document is automatisch "
        "gemaakt voor een importtest."
    )

    document.add_paragraph(
        f"Unieke testcode: {token}"
    )

    document.add_paragraph(
        "Deze tekst moet exact terug te "
        "vinden zijn na de import."
    )

    document.save(path)


# ============================================
# TESTHULPFUNCTIES
# ============================================

def check(condition, message):

    if not condition:

        raise AssertionError(
            message
        )

    print(
        f"[OK] {message}"
    )


def show_error(error):

    if isinstance(
        error,
        urllib.error.HTTPError,
    ):

        response = error.read().decode(
            "utf-8",
            errors="replace",
        )

        print(
            f"\nHTTP ERROR {error.code}"
        )

        print(response)

    else:

        print(
            f"\nERROR: {error}"
        )


# ============================================
# HOOFDTEST
# ============================================

def main():

    print()
    print("=" * 55)
    print("ASTRA AI - MISSIE 7 DOCX-TEST")
    print("=" * 55)

    print(
        f"API: {API_URL}"
    )

    print(
        f"Chat: {CHAT_ID}"
    )

    print(
        f"Aflevering: S{SEASON}E{EPISODE}"
    )

    # ----------------------------------------
    # 1. API CONTROLEREN
    # ----------------------------------------

    print(
        "\n[1/7] API controleren..."
    )

    health = api_get(
        "/health"
    )

    check(
        health.get("status") == "ok",
        "Astra API is bereikbaar.",
    )

    # ----------------------------------------
    # 2. BESTAANDE CANON VASTLEGGEN
    # ----------------------------------------

    print(
        "\n[2/7] Bestaande canon controleren..."
    )

    before = api_get(
        f"/chats/{CHAT_ID}/episodes"
    )

    episodes_before = (
        before.get("episodes", [])
    )

    matching_before = [
        item
        for item in episodes_before
        if item.get("season") == SEASON
        and item.get("episode") == EPISODE
    ]

    check(
        len(matching_before) == 1,
        "Testaflevering bestaat.",
    )

    old_canon_version_id = (
        matching_before[0].get(
            "canon_version_id"
        )
    )

    print(
        "Bestaande canonversie:",
        old_canon_version_id,
    )

    # ----------------------------------------
    # 3. UNIEK TESTDOCUMENT MAKEN
    # ----------------------------------------

    print(
        "\n[3/7] DOCX-document maken..."
    )

    token = uuid.uuid4().hex

    version = (
        "docx-test-"
        + token[:12]
    )

    with tempfile.TemporaryDirectory() as folder:

        path = (
            Path(folder)
            / "astra_import_test.docx"
        )

        make_test_document(
            path,
            token,
        )

        check(
            path.exists(),
            "DOCX-document aangemaakt.",
        )

        # ------------------------------------
        # 4. DOCUMENT IMPORTEREN
        # ------------------------------------

        print(
            "\n[4/7] DOCX uploaden..."
        )

        status, result = multipart_upload(
            "/imports/file",
            {
                "chat_id": CHAT_ID,
                "season": SEASON,
                "episode": EPISODE,
                "version": version,
                "title": "Astra DOCX Import Test",
                "make_canon": "false",
            },
            path,
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        check(
            status in (200, 201),
            "Importverzoek geslaagd.",
        )

        check(
            result.get("success") is True,
            "Import bevestigd door Astra.",
        )

        version_id = result.get(
            "version_id"
        )

        check(
            isinstance(version_id, int),
            "Nieuwe versie-ID ontvangen.",
        )

        # ------------------------------------
        # 5. OPGESLAGEN TEKST CONTROLEREN
        # ------------------------------------

        print(
            "\n[5/7] Opgeslagen tekst controleren..."
        )

        saved = api_get(
            f"/versions/{version_id}"
        )

        saved_text = saved.get(
            "transcript_text",
            ""
        )

        check(
            token in saved_text,
            "Unieke testcode staat in database.",
        )

        check(
            "Deze tekst moet exact terug"
            in saved_text,
            "DOCX-tekst correct uitgelezen.",
        )

        # ------------------------------------
        # 6. CANON NIET OVERSCHREVEN
        # ------------------------------------

        print(
            "\n[6/7] Canonbescherming controleren..."
        )

        check(
            not bool(
                saved.get("is_canon")
            ),
            "Nieuwe testversie is geen canon.",
        )

        after = api_get(
            f"/chats/{CHAT_ID}/episodes"
        )

        matching_after = [
            item
            for item in after.get(
                "episodes",
                []
            )
            if item.get("season") == SEASON
            and item.get("episode") == EPISODE
        ]

        check(
            len(matching_after) == 1,
            "Aflevering bestaat nog steeds.",
        )

        new_canon_version_id = (
            matching_after[0].get(
                "canon_version_id"
            )
        )

        check(
            new_canon_version_id
            == old_canon_version_id,
            "Bestaande canon is behouden.",
        )

        # ------------------------------------
        # 7. EINDRESULTAAT
        # ------------------------------------

        print(
            "\n[7/7] Eindcontrole..."
        )

        print()
        print("=" * 55)
        print("MISSIE 7 - DOCX-IMPORT GESLAAGD!")
        print("=" * 55)

        print(
            f"Nieuwe versie: {version}"
        )

        print(
            f"Versie-ID: {version_id}"
        )

        print(
            f"Canon behouden: JA"
        )

        print(
            f"Tekst opgeslagen: JA"
        )

        print(
            f"Unieke testcode: {token}"
        )

        print()
        print(
            "LET OP: De geïmporteerde "
            "testversie blijft opgeslagen "
            "als niet-canonversie."
        )


# ============================================
# START
# ============================================

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        show_error(error)

        print(
            "\nMISSIE 7 DOCX-TEST NIET GESLAAGD."
        )

        sys.exit(1)
