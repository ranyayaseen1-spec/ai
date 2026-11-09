
"""
ASTRA AI - MISSIE 8
BACK-UPS, HERSTEL EN BACKEND-EINDTESTS
======================================

Geen hardcoded verhalen, personages,
verboden zinnen of taalwoordenlijsten.

GEBRUIK
-------

Eindtests:
    python ai/astra_missie8.py test

Back-up maken:
    python ai/astra_missie8.py backup

Back-ups bekijken:
    python ai/astra_missie8.py list

Back-ups controleren:
    python ai/astra_missie8.py verify

Automatische back-ups (elke 60 minuten):
    python ai/astra_missie8.py auto --minutes 60

Herstellen:
    1. Stop eerst de Astra API-server.
    2. Bekijk de beschikbare back-ups:
       python ai/astra_missie8.py list
    3. Herstel de gewenste back-up:
       python ai/astra_missie8.py restore BESTANDSNAAM --confirm

BELANGRIJK
----------
- De SQLite Backup API maakt consistente kopieën.
- Back-ups worden na het maken gecontroleerd.
- Herstel maakt eerst een veiligheidskopie.
- Herstel weigert te starten als Astra's API
  nog bereikbaar is.
- De bestaande database wordt niet verwijderd
  bij het maken van een back-up.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from datetime import datetime, timezone
from pathlib import Path


# ============================================
# PROJECT EN DATABASE
# ============================================

AI_DIR = Path(__file__).resolve().parent

if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from database import DB_PATH


DATABASE = Path(DB_PATH).resolve()

BACKUP_DIR = (
    DATABASE.parent
    / "astra_backups"
)

API_URL = os.environ.get(
    "ASTRA_API_URL",
    "http://127.0.0.1:8001",
).rstrip("/")

BACKUP_PREFIX = "astra_backup"

REQUEST_TIMEOUT = 10


# ============================================
# HULPFUNCTIES
# ============================================

def timestamp():

    return datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S_%f")


def print_title(title):

    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def ensure_backup_directory():

    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


def file_hash(path):

    digest = hashlib.sha256()

    with open(path, "rb") as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def connect_readonly(path):

    return sqlite3.connect(
        path.as_uri() + "?mode=ro",
        uri=True,
        timeout=30,
    )


def database_integrity(path):

    if not path.is_file():
        return False, "Databasebestand bestaat niet."

    try:

        with connect_readonly(path) as db:

            rows = db.execute(
                "PRAGMA integrity_check"
            ).fetchall()

            result = [
                row[0]
                for row in rows
            ]

            if result == ["ok"]:
                return True, "ok"

            return (
                False,
                "; ".join(
                    str(item)
                    for item in result
                ),
            )

    except sqlite3.Error as error:

        return False, str(error)


def database_summary(path):

    with connect_readonly(path) as db:

        tables = db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        names = [
            row[0]
            for row in tables
        ]

        counts = {}

        for name in names:

            safe_name = (
                '"'
                + name.replace('"', '""')
                + '"'
            )

            count = db.execute(
                f"SELECT COUNT(*) FROM {safe_name}"
            ).fetchone()[0]

            counts[name] = count

    return counts


def write_json_atomic(path, data):

    temporary = path.with_name(
        path.name + ".tmp"
    )

    try:

        temporary.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        os.replace(
            temporary,
            path,
        )

    finally:

        if temporary.exists():
            temporary.unlink()


# ============================================
# VEILIGE BACK-UP
# ============================================

def create_backup():

    print_title(
        "ASTRA - NIEUWE BACK-UP"
    )

    if not DATABASE.is_file():

        raise RuntimeError(
            "De originele database is "
            "niet gevonden:\n"
            f"{DATABASE}"
        )

    ensure_backup_directory()

    unique_name = (
        f"{BACKUP_PREFIX}_"
        f"{timestamp()}_"
        f"{uuid.uuid4().hex[:8]}.db"
    )

    destination = (
        BACKUP_DIR
        / unique_name
    )

    temporary = (
        BACKUP_DIR
        / f".{unique_name}.partial"
    )

    manifest_path = (
        BACKUP_DIR
        / f"{unique_name}.json"
    )

    print(
        f"Database: {DATABASE}"
    )

    print(
        f"Back-up: {destination}"
    )

    try:

        # De SQLite Backup API maakt een
        # consistente momentopname, ook
        # wanneer SQLite WAL gebruikt.
        with sqlite3.connect(
            DATABASE,
            timeout=30,
        ) as source:

            with sqlite3.connect(
                temporary,
                timeout=30,
            ) as target:

                source.backup(
                    target,
                    pages=100,
                    sleep=0.1,
                )

        valid, reason = (
            database_integrity(
                temporary
            )
        )

        if not valid:

            raise RuntimeError(
                "Back-up niet geldig: "
                + reason
            )

        summary = database_summary(
            temporary
        )

        checksum = file_hash(
            temporary
        )

        size = temporary.stat().st_size

        # Publiceer pas na een geslaagde
        # integriteitscontrole.
        os.replace(
            temporary,
            destination,
        )

        manifest = {
            "format": 1,
            "created_at_utc": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            "filename": unique_name,
            "size_bytes": size,
            "sha256": checksum,
            "table_counts": summary,
        }

        write_json_atomic(
            manifest_path,
            manifest,
        )

        print()
        print("[OK] Back-up aangemaakt.")
        print("[OK] SQLite-integriteit gecontroleerd.")
        print("[OK] SHA-256 opgeslagen.")
        print()
        print(
            f"Bestand: {unique_name}"
        )
        print(
            f"Grootte: {size:,} bytes"
        )
        print(
            f"Tabellen: {len(summary)}"
        )

        return destination

    finally:

        if temporary.exists():
            temporary.unlink()


# ============================================
# BACK-UPS BEKIJKEN
# ============================================

def backup_files():

    ensure_backup_directory()

    return sorted(
        BACKUP_DIR.glob(
            f"{BACKUP_PREFIX}_*.db"
        ),
        reverse=True,
    )


def list_backups():

    print_title(
        "ASTRA - BESCHIKBARE BACK-UPS"
    )

    files = backup_files()

    if not files:

        print(
            "Er zijn nog geen back-ups."
        )

        return

    for index, path in enumerate(
        files,
        start=1,
    ):

        print(
            f"{index}. {path.name}"
        )

        print(
            f"   {path.stat().st_size:,} bytes"
        )

    print()
    print(
        f"Map: {BACKUP_DIR}"
    )


# ============================================
# BACK-UPS CONTROLEREN
# ============================================

def verify_one_backup(path):

    valid, reason = (
        database_integrity(path)
    )

    if not valid:

        return False, reason

    manifest_path = (
        BACKUP_DIR
        / f"{path.name}.json"
    )

    if not manifest_path.is_file():

        return (
            False,
            "Metadata met checksum ontbreekt.",
        )

    try:

        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        ValueError,
    ):

        return (
            False,
            "Metadata is niet leesbaar.",
        )

    if manifest.get("filename") != path.name:

        return (
            False,
            "Bestandsnaam komt niet overeen.",
        )

    actual_hash = file_hash(path)

    if (
        actual_hash
        != manifest.get("sha256")
    ):

        return (
            False,
            "SHA-256 komt niet overeen.",
        )

    if (
        path.stat().st_size
        != manifest.get("size_bytes")
    ):

        return (
            False,
            "Bestandsgrootte is gewijzigd.",
        )

    return True, "SQLite en checksum OK"


def verify_backups():

    print_title(
        "ASTRA - BACK-UPCONTROLE"
    )

    files = backup_files()

    if not files:

        print(
            "[LET OP] Nog geen back-ups."
        )

        return False

    all_valid = True

    for path in files:

        valid, reason = (
            verify_one_backup(path)
        )

        label = (
            "OK"
            if valid
            else "FOUT"
        )

        print(
            f"[{label}] {path.name}"
        )

        print(
            f"     {reason}"
        )

        if not valid:
            all_valid = False

    return all_valid


# ============================================
# CONTROLEREN OF SERVER DRAAIT
# ============================================

def api_is_running():

    try:

        with urllib.request.urlopen(
            API_URL + "/health",
            timeout=3,
        ):

            return True

    except urllib.error.HTTPError:

        # Er antwoordt nog steeds een server.
        return True

    except (
        urllib.error.URLError,
        TimeoutError,
    ):

        return False


# ============================================
# VEILIG HERSTELLEN
# ============================================

def resolve_backup(filename):

    # Alleen bestanden in de eigen
    # back-upmap zijn toegestaan.
    name = Path(filename).name

    if name != filename:

        raise RuntimeError(
            "Gebruik uitsluitend de "
            "bestandsnaam uit 'list'."
        )

    path = (
        BACKUP_DIR
        / name
    ).resolve()

    if (
        not name.startswith(
            BACKUP_PREFIX + "_"
        )
        or not name.endswith(".db")
    ):

        raise RuntimeError(
            "Ongeldige back-upnaam."
        )

    if path.parent != BACKUP_DIR.resolve():

        raise RuntimeError(
            "Back-up staat buiten "
            "de toegestane map."
        )

    if not path.is_file():

        raise RuntimeError(
            "Back-upbestand niet gevonden."
        )

    return path


def restore_backup(
    filename,
    confirmed=False,
):

    print_title(
        "ASTRA - DATABASE HERSTELLEN"
    )

    if not confirmed:

        raise RuntimeError(
            "Herstellen vereist --confirm. "
            "Er is niets gewijzigd."
        )

    if api_is_running():

        raise RuntimeError(
            "STOP EERST DE ASTRA-SERVER "
            "MET CTRL + C. "
            "Herstellen is nu geblokkeerd."
        )

    selected = resolve_backup(
        filename
    )

    valid, reason = (
        verify_one_backup(
            selected
        )
    )

    if not valid:

        raise RuntimeError(
            "Geselecteerde back-up is "
            "niet betrouwbaar: "
            + reason
        )

    # Een tweede waarschuwing:
    # de huidige database wordt vervangen.
    print(
        f"Te herstellen: {selected.name}"
    )

    print(
        f"Huidige database: {DATABASE}"
    )

    confirmation = input(
        "\nTyp HERSTEL om door te gaan: "
    ).strip()

    if confirmation != "HERSTEL":

        print(
            "Geannuleerd. Niets gewijzigd."
        )

        return

    safety_copy = None

    if DATABASE.exists():

        # Maak altijd eerst een extra
        # veiligheidskopie van de
        # huidige database.
        safety_copy = create_backup()

        print(
            "\nVeiligheidskopie gemaakt:"
        )

        print(
            safety_copy.name
        )

    temporary_restore = (
        DATABASE.parent
        / (
            ".astra_restore_"
            + uuid.uuid4().hex
            + ".db"
        )
    )

    try:

        shutil.copy2(
            selected,
            temporary_restore,
        )

        valid, reason = (
            database_integrity(
                temporary_restore
            )
        )

        if not valid:

            raise RuntimeError(
                "Herstelkopie is beschadigd: "
                + reason
            )

        # Controleer vlak voor vervangen
        # opnieuw of de API niet draait.
        if api_is_running():

            raise RuntimeError(
                "Astra is opnieuw gestart. "
                "Herstel afgebroken."
            )

        os.replace(
            temporary_restore,
            DATABASE,
        )

        # De herstelde kopie is een
        # zelfstandige SQLite-database.
        # Eventuele oude WAL/SHM-bestanden
        # mogen niet bij deze nieuwe
        # database blijven horen.
        for suffix in (
            "-wal",
            "-shm",
        ):

            sidecar = Path(
                str(DATABASE) + suffix
            )

            if sidecar.exists():

                sidecar.unlink()

        valid, reason = (
            database_integrity(
                DATABASE
            )
        )

        if not valid:

            raise RuntimeError(
                "Integriteitscontrole na "
                "herstel mislukt: "
                + reason
            )

        print()
        print(
            "[OK] DATABASE HERSTELD!"
        )

        if safety_copy:

            print(
                "Vorige database bewaard als:"
            )

            print(
                safety_copy.name
            )

        print(
            "Je kunt Astra nu weer starten."
        )

    finally:

        if temporary_restore.exists():
            temporary_restore.unlink()


# ============================================
# HTTP-EINDTESTS
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

        return (
            response.status,
            json.loads(
                response.read().decode(
                    "utf-8"
                )
            ),
        )


def test_api():

    print_title(
        "ASTRA - BACKEND EINDTESTS"
    )

    if not api_is_running():

        raise RuntimeError(
            "Astra's API draait niet. "
            "Start eerst in een andere terminal:\n"
            "python ai/astra_api.py"
        )

    tests = [
        (
            "Database en API",
            "/health",
        ),
        (
            "Importmodule",
            "/imports/status",
        ),
        (
            "Canon Engine",
            "/astra/status",
        ),
        (
            "AU-chats",
            "/chats",
        ),
        (
            "Verboden-zinnenregels",
            "/astra/rules",
        ),
        (
            "API-specificatie",
            "/openapi.json",
        ),
    ]

    passed = 0
    failed = 0

    for name, path in tests:

        try:

            status, data = api_get(
                path
            )

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            if path == "/health":

                if data.get("status") != "ok":

                    raise RuntimeError(
                        "Health-status niet OK."
                    )

            if path == "/astra/status":

                if data.get("status") != "ready":

                    raise RuntimeError(
                        "Canon Engine niet klaar."
                    )

            if path == "/astra/rules":

                if not isinstance(
                    data.get("rules"),
                    list,
                ):

                    raise RuntimeError(
                        "Regellijst ontbreekt."
                    )

            if path == "/openapi.json":

                paths = data.get(
                    "paths",
                    {}
                )

                required = (
                    "/astra/chat",
                    "/astra/rules",
                    "/imports/file",
                    "/imports/paste",
                )

                missing = [
                    item
                    for item in required
                    if item not in paths
                ]

                if missing:

                    raise RuntimeError(
                        "Ontbrekende routes: "
                        + ", ".join(missing)
                    )

            print(
                f"[OK] {name}"
            )

            passed += 1

        except Exception as error:

            print(
                f"[FOUT] {name}: {error}"
            )

            failed += 1

    print()
    print(
        f"Geslaagd: {passed}"
    )

    print(
        f"Mislukt: {failed}"
    )

    return failed == 0


# ============================================
# DATABASE-EINDTESTS
# ============================================

def test_database():

    print_title(
        "ASTRA - DATABASE EINDTESTS"
    )

    valid, reason = (
        database_integrity(
            DATABASE
        )
    )

    if not valid:

        print(
            "[FOUT] Database-integriteit:"
        )

        print(reason)

        return False

    print(
        "[OK] Database-integriteit"
    )

    summary = database_summary(
        DATABASE
    )

    print()
    print(
        "Opgeslagen gegevens per tabel:"
    )

    for name, count in summary.items():

        print(
            f"  {name}: {count}"
        )

    return True


# ============================================
# AUTOMATISCHE BACK-UPS
# ============================================

def automatic_backups(minutes):

    if minutes <= 0:

        raise RuntimeError(
            "Interval moet groter "
            "zijn dan nul."
        )

    print_title(
        "ASTRA - AUTOMATISCHE BACK-UPS"
    )

    print(
        f"Interval: {minutes} minuten"
    )

    print(
        "Laat deze terminal open."
    )

    print(
        "Stoppen: Ctrl + C"
    )

    # Maak meteen een eerste back-up.
    while True:

        try:

            create_backup()

        except Exception as error:

            print(
                "[FOUT] Automatische back-up:"
            )

            print(error)

        try:

            time.sleep(
                minutes * 60
            )

        except KeyboardInterrupt:

            print(
                "\nAutomatische back-ups gestopt."
            )

            break


# ============================================
# COMMANDOREGELS
# ============================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Astra AI - Missie 8: "
            "back-ups en eindtests"
        )
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "backup"
    )

    commands.add_parser(
        "list"
    )

    commands.add_parser(
        "verify"
    )

    commands.add_parser(
        "test"
    )

    commands.add_parser(
        "test-db"
    )

    auto_parser = commands.add_parser(
        "auto"
    )

    auto_parser.add_argument(
        "--minutes",
        type=float,
        default=60,
    )

    restore_parser = commands.add_parser(
        "restore"
    )

    restore_parser.add_argument(
        "filename"
    )

    restore_parser.add_argument(
        "--confirm",
        action="store_true",
    )

    args = parser.parse_args()

    print()
    print(
        "ASTRA AI - MISSIE 8"
    )

    print(
        f"Database: {DATABASE}"
    )

    if args.command == "backup":

        create_backup()

    elif args.command == "list":

        list_backups()

    elif args.command == "verify":

        if not verify_backups():
            sys.exit(1)

    elif args.command == "test":

        database_ok = test_database()

        api_ok = test_api()

        if not (
            database_ok and api_ok
        ):

            sys.exit(1)

        print_title(
            "ASTRA - EINDTESTS GESLAAGD"
        )

    elif args.command == "test-db":

        if not test_database():
            sys.exit(1)

    elif args.command == "auto":

        automatic_backups(
            args.minutes
        )

    elif args.command == "restore":

        restore_backup(
            args.filename,
            confirmed=args.confirm,
        )


if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nGestopt door gebruiker."
        )

    except Exception as error:

        print()
        print(
            "[FOUT] MISSIE 8:"
        )

        print(error)

        sys.exit(1)
