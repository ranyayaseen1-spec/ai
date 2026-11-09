
"""
ASTRA AI — MISSIE 7
VERHALEN IMPORTEREN

Ondersteunt:
1. Tekst kopiëren en plakken
2. DOCX-bestanden
3. TXT-bestanden
4. PDF-bestanden met selecteerbare tekst

Eigenschappen:
- Meerdere AU-chats
- Seizoenen en afleveringen
- Meerdere verhaalversies
- Canonbescherming
- Geen automatische overschrijving
- Semantische geheugenindex bijwerken
- Nederlands en Engels
- Lange verhalen bewaren

Installatie:
    python -m pip install python-docx pypdf python-multipart

Voeg de router toe aan astra_api.py:
    from astra_import import router as import_router
    app.include_router(import_router)

Start daarna:
    python ai/astra_api.py
"""

import io
import sqlite3

from contextlib import closing
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from pydantic import BaseModel, Field
from docx import Document
from pypdf import PdfReader

import database
import semantic_memory


# ==========================================
# INSTELLINGEN
# ==========================================

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_STORY_CHARACTERS = 2_000_000

SUPPORTED_EXTENSIONS = {
    ".docx",
    ".txt",
    ".pdf",
}

router = APIRouter(
    prefix="/imports",
    tags=["Missie 7 - Verhalen importeren"],
)


# ==========================================
# REQUESTMODEL
# ==========================================

class PasteStoryRequest(BaseModel):

    chat_id: int = Field(ge=1)

    season: int = Field(ge=1)

    episode: int = Field(ge=1)

    version: str = Field(
        min_length=1,
        max_length=100,
    )

    title: str = Field(
        default="",
        max_length=300,
    )

    text: str = Field(
        min_length=1,
    )

    make_canon: bool = False


# ==========================================
# DATABASE
# ==========================================

def connect_database():

    connection = sqlite3.connect(
        database.DB_PATH,
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


# ==========================================
# TEKST CONTROLEREN
# ==========================================

def validate_story(
    chat_id,
    season,
    episode,
    version,
    title,
    text,
):

    if chat_id < 1:
        raise HTTPException(
            status_code=422,
            detail="Ongeldige chat_id.",
        )

    if season < 1 or episode < 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "Seizoen en aflevering "
                "moeten minimaal 1 zijn."
            ),
        )

    version = str(version).strip()
    title = str(title or "").strip()
    text = str(text or "").strip()

    if not version or len(version) > 100:
        raise HTTPException(
            status_code=422,
            detail="Ongeldig versienummer.",
        )

    if len(title) > 300:
        raise HTTPException(
            status_code=422,
            detail="Titel is te lang.",
        )

    if not text:
        raise HTTPException(
            status_code=422,
            detail="Het verhaal is leeg.",
        )

    if len(text) > MAX_STORY_CHARACTERS:
        raise HTTPException(
            status_code=413,
            detail=(
                "Het verhaal is te groot "
                "voor één import."
            ),
        )

    return version, title, text


# ==========================================
# DOCUMENTEN LEZEN
# ==========================================

def read_txt(data):

    for encoding in (
        "utf-8-sig",
        "utf-16",
    ):

        try:
            return data.decode(encoding)

        except UnicodeError:
            continue

    raise HTTPException(
        status_code=422,
        detail=(
            "Kan het tekstbestand niet lezen. "
            "Sla het bestand op als UTF-8."
        ),
    )


def read_docx(data):

    try:
        document = Document(
            io.BytesIO(data)
        )

        paragraphs = []

        # Documentvolgorde van gewone paragrafen
        # en tabellen behouden.
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        body = document.element.body

        for child in body.iterchildren():

            if child.tag == qn("w:p"):

                paragraph = Paragraph(
                    child,
                    document,
                )

                paragraphs.append(
                    paragraph.text
                )

            elif child.tag == qn("w:tbl"):

                table = Table(
                    child,
                    document,
                )

                for row in table.rows:

                    cells = [
                        cell.text.strip()
                        for cell in row.cells
                    ]

                    paragraphs.append(
                        " | ".join(cells)
                    )

        return "\n".join(paragraphs)

    except Exception as error:

        raise HTTPException(
            status_code=422,
            detail=(
                "Dit DOCX-bestand kon "
                "niet gelezen worden."
            ),
        ) from error


def read_pdf(data):

    try:

        reader = PdfReader(
            io.BytesIO(data)
        )

        pages = []

        for page in reader.pages:

            pages.append(
                page.extract_text() or ""
            )

        text = "\n\n".join(pages)

        if not text.strip():

            raise HTTPException(
                status_code=422,
                detail=(
                    "Deze PDF bevat geen "
                    "uitleesbare tekst. "
                    "Een gescande PDF heeft "
                    "eerst OCR nodig."
                ),
            )

        return text

    except HTTPException:
        raise

    except Exception as error:

        raise HTTPException(
            status_code=422,
            detail=(
                "Deze PDF kon niet "
                "gelezen worden."
            ),
        ) from error


def extract_document(
    filename,
    data,
):

    extension = Path(
        filename or ""
    ).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:

        raise HTTPException(
            status_code=415,
            detail=(
                "Bestandstype niet ondersteund. "
                "Gebruik DOCX, TXT of PDF."
            ),
        )

    if not data:

        raise HTTPException(
            status_code=422,
            detail="Het bestand is leeg.",
        )

    if len(data) > MAX_FILE_BYTES:

        raise HTTPException(
            status_code=413,
            detail="Het bestand is te groot.",
        )

    if extension == ".txt":
        return read_txt(data)

    if extension == ".docx":
        return read_docx(data)

    if extension == ".pdf":
        return read_pdf(data)

    raise HTTPException(
        status_code=415,
        detail="Onbekend bestandstype.",
    )


# ==========================================
# VERHAAL OPSLAAN
# ==========================================

def save_imported_story(
    chat_id,
    season,
    episode,
    version,
    title,
    text,
    make_canon=False,
    import_method="paste",
):

    version, title, text = validate_story(
        chat_id,
        season,
        episode,
        version,
        title,
        text,
    )

    # Maak tabellen aan als dat nodig is.
    semantic_memory.initialize_semantic_memory()

    with closing(connect_database()) as db:

        try:

            # BEGIN IMMEDIATE voorkomt dat twee
            # gelijktijdige imports dezelfde
            # aflevering/versie aanmaken.
            db.execute(
                "BEGIN IMMEDIATE"
            )

            chat = db.execute(
                """
                SELECT chat_id, title
                FROM conversations
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()

            if chat is None:

                raise HTTPException(
                    status_code=404,
                    detail=(
                        "Deze AU-chat "
                        "bestaat niet."
                    ),
                )

            episode_row = db.execute(
                """
                SELECT episode_id
                FROM episodes
                WHERE chat_id = ?
                  AND season_id = ?
                  AND episode_number = ?
                """,
                (
                    chat_id,
                    season,
                    episode,
                ),
            ).fetchone()

            created_episode = False

            if episode_row is None:

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
                        season,
                        episode,
                    ),
                )

                episode_id = cursor.lastrowid
                created_episode = True

            else:

                episode_id = (
                    episode_row["episode_id"]
                )

            existing_version = db.execute(
                """
                SELECT version_id
                FROM episode_versions
                WHERE episode_id = ?
                  AND version_number = ?
                """,
                (
                    episode_id,
                    version,
                ),
            ).fetchone()

            if existing_version is not None:

                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Deze versie bestaat al. "
                        "Er is niets overschreven. "
                        "Kies een nieuw versienummer."
                    ),
                )

            if make_canon:

                # Canonwissel en nieuwe versie
                # gebeuren in dezelfde transactie.
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
                    version,
                    text,
                    int(make_canon),
                ),
            )

            version_id = cursor.lastrowid

            db.commit()

        except HTTPException:

            db.rollback()
            raise

        except sqlite3.IntegrityError as error:

            db.rollback()

            raise HTTPException(
                status_code=409,
                detail=(
                    "Databaseconflict. "
                    "Controleer aflevering "
                    "en versienummer."
                ),
            ) from error

        except Exception:

            db.rollback()
            raise

    # De originele tekst staat nu veilig
    # opgeslagen in SQLite.
    #
    # Daarna proberen we de semantische
    # zoekindex meteen bij te werken.
    #
    # Als indexeren faalt, blijft het verhaal
    # opgeslagen en kan later opnieuw
    # geïndexeerd worden.

    indexed = False
    indexing_error = None

    try:

        semantic_memory.update_episode_embeddings(
            chat_id
        )

        indexed = True

    except Exception as error:

        indexing_error = str(error)

    return {
        "success": True,
        "message": (
            "Verhaal succesvol geïmporteerd!"
        ),
        "chat_id": chat_id,
        "au_title": chat["title"],
        "season": season,
        "episode": episode,
        "episode_id": episode_id,
        "version": version,
        "version_id": version_id,
        "title": title,
        "is_canon": bool(make_canon),
        "created_episode": created_episode,
        "import_method": import_method,
        "characters_saved": len(text),
        "lines_saved": len(text.splitlines()),
        "semantic_index_ready": indexed,
        "indexing_error": indexing_error,
        "note": (
            "Het verhaal is volledig in SQLite "
            "opgeslagen. De titel is momenteel "
            "importmetadata in het antwoord; "
            "de bestaande episodes-tabel heeft "
            "geen aparte titelkolom."
        ),
    }


# ==========================================
# ROUTE 1 — TEKST PLAKKEN
# ==========================================

@router.post("/paste", status_code=201)
def import_pasted_text(
    request: PasteStoryRequest,
):

    """
    Importeer tekst die je uit Google Docs
    of een andere teksteditor hebt gekopieerd.
    """

    return save_imported_story(
        chat_id=request.chat_id,
        season=request.season,
        episode=request.episode,
        version=request.version,
        title=request.title,
        text=request.text,
        make_canon=request.make_canon,
        import_method="paste",
    )


# ==========================================
# ROUTE 2 — DOCUMENT IMPORTEREN
# ==========================================

@router.post("/file", status_code=201)
async def import_document(
    chat_id: int = Form(..., ge=1),
    season: int = Form(..., ge=1),
    episode: int = Form(..., ge=1),
    version: str = Form(...),
    title: str = Form(""),
    make_canon: bool = Form(False),
    file: UploadFile = File(...),
):

    """
    Upload een DOCX-, TXT- of PDF-bestand.

    Google Docs:
    Bestand > Downloaden >
    Microsoft Word (.docx)
    """

    try:

        data = await file.read(
            MAX_FILE_BYTES + 1
        )

    finally:

        await file.close()

    if len(data) > MAX_FILE_BYTES:

        raise HTTPException(
            status_code=413,
            detail=(
                "Bestand is groter dan 15 MB."
            ),
        )

    text = extract_document(
        file.filename,
        data,
    )

    result = save_imported_story(
        chat_id=chat_id,
        season=season,
        episode=episode,
        version=version,
        title=title,
        text=text,
        make_canon=make_canon,
        import_method="file",
    )

    result["filename"] = file.filename

    return result


# ==========================================
# ROUTE 3 — DOCUMENT VOORBEKIJKEN
# ==========================================

@router.post("/preview")
async def preview_document(
    file: UploadFile = File(...),
):

    """
    Bekijk de uitgelezen tekst voordat
    je het verhaal opslaat.
    """

    try:

        data = await file.read(
            MAX_FILE_BYTES + 1
        )

    finally:

        await file.close()

    text = extract_document(
        file.filename,
        data,
    )

    return {
        "filename": file.filename,
        "characters": len(text),
        "lines": len(text.splitlines()),
        "preview": text[:2000],
        "message": (
            "Dit is alleen een voorbeeld. "
            "Er is nog niets opgeslagen."
        ),
    }


# ==========================================
# ROUTE 4 — IMPORTSTATUS
# ==========================================

@router.get("/status")
def import_status():

    return {
        "mission": 7,
        "status": "ready",
        "supported_methods": [
            "paste",
            "file",
        ],
        "supported_files": [
            ".docx",
            ".txt",
            ".pdf",
        ],
        "max_file_mb": 15,
        "max_story_characters": (
            MAX_STORY_CHARACTERS
        ),
        "features": {
            "multiple_aus": True,
            "multiple_seasons": True,
            "multiple_versions": True,
            "canon_protection": True,
            "semantic_indexing": True,
            "automatic_overwrite": False,
        },
    }
