
"""
ASTRA AI - MISSIE 8
Back-ups, herstel en backend-eindtests.

COMMANDO'S

python ai/astra_missie8.py test
python ai/astra_missie8.py test-db
python ai/astra_missie8.py backup
python ai/astra_missie8.py list
python ai/astra_missie8.py verify
python ai/astra_missie8.py auto --minutes 60

Herstel (alleen wanneer de API gestopt is):

python ai/astra_missie8.py restore BESTANDSNAAM.db --confirm

Belangrijk:
- Geen bestaande verhalen of canon worden aangepast bij back-ups.
- Back-ups worden gemaakt met SQLite's backup API.
- Alle SQLite-verbindingen worden expliciet gesloten.
- Tijdelijke back-ups staan buiten OneDrive.
- Een back-up krijgt pas metadata na succesvolle verificatie.
- Herstel maakt eerst een veiligheidskopie.
- Herstel is niet bedoeld voor een database die nog in gebruik is.
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

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# INSTELLINGEN
# ============================================================

AI_DIR = Path(__file__).resolve().parent

if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from database import DB_PATH


DATABASE = Path(DB_PATH).resolve()

BACKUP_DIR = DATABASE.parent / "astra_backups"

BACKUP_PREFIX = "astra_backup"

API_URL = os.environ.get(
    "ASTRA_API_URL",
    "http://127.0.0.1:8001"
).rstrip("/")

REQUEST_TIMEOUT = 10


# ============================================================
# ALGEMENE HULPFUNCTIES
# ============================================================

def print_title(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def timestamp():
    return datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S_%f")


def ensure_backup_directory():
    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def file_hash(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def connect_readonly(path):
    path = Path(path).resolve()

    return sqlite3.connect(
        path.as_uri() + "?mode=ro",
        uri=True,
        timeout=30
    )


def database_integrity(path):
    path = Path(path)

    if not path.is_file():
        return False, "Databasebestand bestaat niet."

    try:
        with closing(connect_readonly(path)) as db:
            rows = db.execute(
                "PRAGMA integrity_check"
            ).fetchall()

        results = [row[0] for row in rows]

        if results == ["ok"]:
            return True, "ok"

        return False, "; ".join(
            str(item) for item in results
        )

    except (sqlite3.Error, OSError) as error:
        return False, str(error)


def database_summary(path):
    counts = {}

    with closing(connect_readonly(path)) as db:
        tables = db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        for (name,) in tables:
            safe_name = '"' + name.replace('"', '""') + '"'

            count = db.execute(
                f"SELECT COUNT(*) FROM {safe_name}"
            ).fetchone()[0]

            counts[name] = count

    return counts


def write_json_atomic(path, data):
    """
    Schrijf JSON eerst naar een tijdelijk bestand.

    Het tijdelijke bestand staat buiten OneDrive.
    Daarna wordt de inhoud naar de doelmap gekopieerd.
    """

    path = Path(path)

    with tempfile.TemporaryDirectory(
        prefix="astra_json_"
    ) as temporary_directory:

        temporary = (
            Path(temporary_directory)
            / "manifest.json"
        )

        temporary.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        shutil.copyfile(temporary, path)


def copy_and_check(source, destination):
    """
    Kopieer een bestand en controleer de SHA-256.

    Het doelbestand wordt nooit als geldig beschouwd
    wanneer de inhoud afwijkt.
    """

    expected_hash = file_hash(source)

    shutil.copyfile(source, destination)

    actual_hash = file_hash(destination)

    if actual_hash != expected_hash:
        raise RuntimeError(
            "Kopie mislukt: SHA-256 komt niet overeen."
        )

    return actual_hash


# ============================================================
# BACK-UP MAKEN
# ============================================================

def create_backup():
    print_title("ASTRA - NIEUWE BACK-UP")

    if not DATABASE.is_file():
        raise RuntimeError(
            "Database niet gevonden:\n"
            f"{DATABASE}"
        )

    ensure_backup_directory()

    unique_name = (
        f"{BACKUP_PREFIX}_"
        f"{timestamp()}_"
        f"{uuid.uuid4().hex[:8]}.db"
    )

    destination = BACKUP_DIR / unique_name

    manifest_path = (
        BACKUP_DIR / f"{unique_name}.json"
    )

    print(f"Database: {DATABASE}")
    print(f"Back-up: {destination}")

    published = False

    try:
        # Gebruik een tijdelijke werkmap buiten OneDrive.
        with tempfile.TemporaryDirectory(
            prefix="astra_sqlite_backup_"
        ) as temporary_directory:

            temporary = (
                Path(temporary_directory)
                / "backup.db"
            )

            # Beide verbindingen worden expliciet gesloten.
            with closing(
                sqlite3.connect(
                    DATABASE,
                    timeout=30
                )
            ) as source:

                with closing(
                    sqlite3.connect(
                        temporary,
                        timeout=30
                    )
                ) as target:

                    source.backup(
                        target,
                        pages=100,
                        sleep=0.1
                    )

            # Controleer de lokale momentopname.
            valid, reason = database_integrity(
                temporary
            )

            if not valid:
                raise RuntimeError(
                    "Tijdelijke back-up is ongeldig: "
                    + reason
                )

            summary = database_summary(
                temporary
            )

            size = temporary.stat().st_size

            checksum = copy_and_check(
                temporary,
                destination
            )

            # Controleer ook de definitieve kopie.
            valid, reason = database_integrity(
                destination
            )

            if not valid:
                raise RuntimeError(
                    "Definitieve back-up is ongeldig: "
                    + reason
                )

            if destination.stat().st_size != size:
                raise RuntimeError(
                    "Bestandsgrootte is gewijzigd."
                )

            manifest = {
                "format": 1,
                "created_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "filename": unique_name,
                "size_bytes": size,
                "sha256": checksum,
                "table_counts": summary
            }

            write_json_atomic(
                manifest_path,
                manifest
            )

            published = True

        print()
        print("[OK] Back-up aangemaakt.")
        print("[OK] SQLite-integriteit gecontroleerd.")
        print("[OK] SHA-256 gecontroleerd.")
        print()
        print(f"Bestand: {unique_name}")
        print(f"Grootte: {size:,} bytes")
        print(f"Tabellen: {len(summary)}")

        return destination

    finally:
        if not published:
            # Verwijder een onvolledige doelkopie
            # alleen als Windows dit toestaat.
            for path in (manifest_path, destination):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    print(
                        "[WAARSCHUWING] Onvolledig bestand "
                        f"kon niet worden verwijderd: {path.name}"
                    )


# ============================================================
# BACK-UPS BEKIJKEN
# ============================================================

def backup_files():
    ensure_backup_directory()

    return sorted(
        BACKUP_DIR.glob(
            f"{BACKUP_PREFIX}_*.db"
        ),
        reverse=True
    )


def list_backups():
    print_title("ASTRA - BESCHIKBARE BACK-UPS")

    files = backup_files()

    if not files:
        print("Er zijn nog geen back-ups.")
        return

    for index, path in enumerate(files, start=1):
        print(f"{index}. {path.name}")
        print(f"   {path.stat().st_size:,} bytes")

        manifest = (
            BACKUP_DIR / f"{path.name}.json"
        )

        if manifest.exists():
            print("   Metadata: aanwezig")
        else:
            print("   Metadata: ontbreekt")

    print()
    print(f"Map: {BACKUP_DIR}")


# ============================================================
# BACK-UPS CONTROLEREN
# ============================================================

def verify_one_backup(path):
    path = Path(path)

    valid, reason = database_integrity(path)

    if not valid:
        return False, reason

    manifest_path = (
        BACKUP_DIR / f"{path.name}.json"
    )

    if not manifest_path.is_file():
        return False, "Metadata met checksum ontbreekt."

    try:
        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError) as error:
        return False, f"Metadata niet leesbaar: {error}"

    if manifest.get("filename") != path.name:
        return False, "Bestandsnaam komt niet overeen."

    if manifest.get("size_bytes") != path.stat().st_size:
        return False, "Bestandsgrootte komt niet overeen."

    if manifest.get("sha256") != file_hash(path):
        return False, "SHA-256 komt niet overeen."

    return True, "SQLite en checksum OK"


def verify_backups():
    print_title("ASTRA - BACK-UPCONTROLE")

    files = backup_files()

    if not files:
        print("[LET OP] Nog geen back-ups.")
        return False

    all_valid = True

    for path in files:
        valid, reason = verify_one_backup(path)

        label = "OK" if valid else "FOUT"

        print(f"[{label}] {path.name}")
        print(f"     {reason}")

        if not valid:
            all_valid = False

    return all_valid


# ============================================================
# API CONTROLEREN
# ============================================================

def api_is_running():
    try:
        with urllib.request.urlopen(
            API_URL + "/health",
            timeout=3
        ):
            return True

    except urllib.error.HTTPError:
        return True

    except (urllib.error.URLError, TimeoutError):
        return False


def api_get(path):
    request = urllib.request.Request(
        API_URL + path,
        method="GET"
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT
    ) as response:

        content = response.read().decode("utf-8")

        return response.status, json.loads(content)


# ============================================================
# HERSTELVEILIGHEID
# ============================================================

def database_is_busy():
    """
    Probeer een exclusieve SQLite-transactie te starten.

    Dit is een aanvullende controle, geen garantie dat
    een ander proces de database niet later opnieuw opent.
    """

    if not DATABASE.exists():
        return False

    connection = None

    try:
        connection = sqlite3.connect(
            DATABASE,
            timeout=0.2,
            isolation_level=None
        )

        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("ROLLBACK")

        return False

    except sqlite3.OperationalError:
        return True

    finally:
        if connection is not None:
            connection.close()


def resolve_backup(filename):
    ensure_backup_directory()

    name = Path(filename).name

    if name != filename:
        raise RuntimeError(
            "Gebruik alleen de bestandsnaam uit 'list'."
        )

    if (
        not name.startswith(BACKUP_PREFIX + "_")
        or not name.endswith(".db")
    ):
        raise RuntimeError("Ongeldige back-upnaam.")

    path = (BACKUP_DIR / name).resolve()

    if path.parent != BACKUP_DIR.resolve():
        raise RuntimeError(
            "Back-up staat buiten de back-upmap."
        )

    if not path.is_file():
        raise RuntimeError(
            "Back-upbestand niet gevonden."
        )

    return path


# ============================================================
# BACK-UP HERSTELLEN
# ============================================================

def restore_backup(filename, confirmed=False):
    print_title("ASTRA - DATABASE HERSTELLEN")

    if not confirmed:
        raise RuntimeError(
            "Herstellen vereist --confirm. "
            "Er is niets gewijzigd."
        )

    if api_is_running():
        raise RuntimeError(
            "Stop eerst de Astra API-server met Ctrl + C."
        )

    if database_is_busy():
        raise RuntimeError(
            "De database is in gebruik. "
            "Sluit alle programma's die haar gebruiken."
        )

    selected = resolve_backup(filename)

    valid, reason = verify_one_backup(selected)

    if not valid:
        raise RuntimeError(
            "Deze back-up is niet geldig: " + reason
        )

    print(f"Back-up: {selected.name}")
    print(f"Database: {DATABASE}")

    confirmation = input(
        "\nTyp HERSTEL om door te gaan: "
    ).strip()

    if confirmation != "HERSTEL":
        print("Geannuleerd. Niets gewijzigd.")
        return

    # Maak een veiligheidskopie voordat we iets vervangen.
    safety_copy = None

    if DATABASE.exists():
        safety_copy = create_backup()

        print()
        print("[OK] Veiligheidskopie gemaakt:")
        print(safety_copy.name)

    # Werk buiten OneDrive aan de herstelkopie.
    with tempfile.TemporaryDirectory(
        prefix="astra_restore_"
    ) as temporary_directory:

        temporary = (
            Path(temporary_directory)
            / "restore.db"
        )

        copy_and_check(selected, temporary)

        valid, reason = database_integrity(temporary)

        if not valid:
            raise RuntimeError(
                "Herstelkopie ongeldig: " + reason
            )

        if api_is_running() or database_is_busy():
            raise RuntimeError(
                "De database is opnieuw in gebruik. "
                "Herstel afgebroken."
            )

        # Weiger een database met actieve WAL-bestanden
        # te vervangen. Sluit eerst alle SQLite-processen.
        wal_file = Path(str(DATABASE) + "-wal")
        shm_file = Path(str(DATABASE) + "-shm")

        if wal_file.exists() or shm_file.exists():
            raise RuntimeError(
                "Er bestaan nog SQLite WAL/SHM-bestanden. "
                "Sluit alle databaseprocessen voordat "
                "je een back-up herstelt. "
                "Verwijder deze bestanden niet handmatig."
            )

        # Kopieer naar een tijdelijk doelbestand
        # naast de originele database.
        staged = (
            DATABASE.parent
            / f".astra_restore_{uuid.uuid4().hex}.db"
        )

        try:
            copy_and_check(temporary, staged)

            valid, reason = database_integrity(staged)

            if not valid:
                raise RuntimeError(
                    "Herstelbestand ongeldig: " + reason
                )

            # Vervang pas na alle controles.
            os.replace(staged, DATABASE)

        finally:
            if staged.exists():
                try:
                    staged.unlink()
                except OSError:
                    print(
                        "[WAARSCHUWING] Tijdelijk "
                        "herstelbestand blijft staan."
                    )

    valid, reason = database_integrity(DATABASE)

    if not valid:
        raise RuntimeError(
            "Integriteitscontrole na herstel mislukt: "
            + reason
        )

    print()
    print("[OK] DATABASE HERSTELD!")

    if safety_copy is not None:
        print("Vorige database bewaard als:")
        print(safety_copy.name)

    print("Je kunt Astra nu opnieuw starten.")


# ============================================================
# DATABASE-EINDTESTS
# ============================================================

def test_database():
    print_title("ASTRA - DATABASE EINDTESTS")

    valid, reason = database_integrity(DATABASE)

    if not valid:
        print("[FOUT] Database-integriteit:")
        print(reason)
        return False

    print("[OK] Database-integriteit")

    try:
        summary = database_summary(DATABASE)

    except Exception as error:
        print(f"[FOUT] Tabellen lezen: {error}")
        return False

    print()
    print("Opgeslagen gegevens per tabel:")

    for name, count in summary.items():
        print(f"  {name}: {count}")

    return True


# ============================================================
# API-EINDTESTS
# ============================================================

def test_api():
    print_title("ASTRA - BACKEND EINDTESTS")

    if not api_is_running():
        print(
            "[FOUT] Astra's API is niet bereikbaar op "
            f"{API_URL}"
        )

        print(
            "Start eerst de server in een andere terminal."
        )

        return False

    tests = [
        ("Database en API", "/health"),
        ("Importmodule", "/imports/status"),
        ("Canon Engine", "/astra/status"),
        ("AU-chats", "/chats"),
        ("Dynamische regels", "/astra/rules"),
        ("API-specificatie", "/openapi.json"),
    ]

    passed = 0
    failed = 0

    for name, path in tests:
        try:
            status, data = api_get(path)

            if status != 200:
                raise RuntimeError(f"HTTP {status}")

            if path == "/health":
                if not isinstance(data, dict):
                    raise RuntimeError(
                        "Ongeldig health-antwoord."
                    )

            if path == "/astra/rules":
                if not isinstance(data, (dict, list)):
                    raise RuntimeError(
                        "Ongeldig regels-antwoord."
                    )

            if path == "/openapi.json":
                paths = data.get("paths", {})

                required = (
                    "/astra/chat",
                    "/astra/rules",
                    "/imports/file",
                    "/imports/paste"
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

            print(f"[OK] {name}")
            passed += 1

        except Exception as error:
            print(f"[FOUT] {name}: {error}")
            failed += 1

    print()
    print(f"Geslaagd: {passed}")
    print(f"Mislukt: {failed}")

    return failed == 0


# ============================================================
# AUTOMATISCHE BACK-UPS
# ============================================================

def automatic_backups(minutes):
    if minutes <= 0:
        raise RuntimeError(
            "Het interval moet groter zijn dan nul."
        )

    print_title("ASTRA - AUTOMATISCHE BACK-UPS")

    print(f"Interval: {minutes} minuten")
    print("Laat deze terminal open.")
    print("Stoppen: Ctrl + C")

    while True:
        try:
            create_backup()

        except Exception as error:
            print()
            print("[FOUT] Automatische back-up:")
            print(error)

        try:
            time.sleep(minutes * 60)

        except KeyboardInterrupt:
            print()
            print("Automatische back-ups gestopt.")
            break


# ============================================================
# COMMANDOREGELS
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Astra AI - Missie 8: "
            "back-ups, herstel en eindtests"
        )
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True
    )

    commands.add_parser("backup")
    commands.add_parser("list")
    commands.add_parser("verify")
    commands.add_parser("test")
    commands.add_parser("test-db")

    auto_parser = commands.add_parser("auto")

    auto_parser.add_argument(
        "--minutes",
        type=float,
        default=60
    )

    restore_parser = commands.add_parser("restore")

    restore_parser.add_argument("filename")

    restore_parser.add_argument(
        "--confirm",
        action="store_true"
    )

    args = parser.parse_args()

    print()
    print("ASTRA AI - MISSIE 8")
    print(f"Database: {DATABASE}")

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

        if not (database_ok and api_ok):
            sys.exit(1)

        print_title("ASTRA - EINDTESTS GESLAAGD")

    elif args.command == "test-db":
        if not test_database():
            sys.exit(1)

    elif args.command == "auto":
        automatic_backups(args.minutes)

    elif args.command == "restore":
        restore_backup(
            args.filename,
            confirmed=args.confirm
        )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\nGestopt door gebruiker.")

    except Exception as error:
        print()
        print("[FOUT] MISSIE 8:")
        print(error)
        sys.exit(1)
