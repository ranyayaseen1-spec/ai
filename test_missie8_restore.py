
"""
ASTRA AI - MISSIE 8
Veilige hersteltest.

Dit script:
- gebruikt een bestaande, geverifieerde back-up;
- herstelt die uitsluitend naar een tijdelijke testdatabase;
- controleert SQLite-integriteit en gegevens;
- verandert de echte database NIET.
"""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from astra_missie8 import (
    DATABASE,
    BACKUP_DIR,
    backup_files,
    database_integrity,
    database_summary,
    verify_one_backup,
)


def sha256(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b""
        ):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    print()
    print("=" * 60)
    print("ASTRA - VEILIGE HERSTELTEST")
    print("=" * 60)

    original_hash_before = sha256(DATABASE)

    print()
    print("[1/7] Originele database gevonden.")
    print(f"Database: {DATABASE}")

    backups = backup_files()

    if not backups:
        raise RuntimeError(
            "Geen back-ups gevonden."
        )

    # Gebruik de nieuwste back-up die volledig
    # door de bestaande verificatie komt.
    selected = None

    for backup in backups:
        valid, reason = verify_one_backup(backup)

        if valid:
            selected = backup
            break

    if selected is None:
        raise RuntimeError(
            "Geen geldige back-up gevonden."
        )

    print()
    print("[2/7] Geverifieerde back-up gevonden.")
    print(f"Back-up: {selected.name}")

    expected_summary = database_summary(selected)
    expected_hash = sha256(selected)

    with tempfile.TemporaryDirectory(
        prefix="astra_restore_test_"
    ) as temporary_directory:

        restored = (
            Path(temporary_directory)
            / "restored_test.db"
        )

        # Simuleer het herstellen naar een nieuwe database.
        shutil.copyfile(selected, restored)

        print()
        print("[3/7] Back-up naar testdatabase hersteld.")

        if sha256(restored) != expected_hash:
            raise RuntimeError(
                "Herstelde database wijkt af van back-up."
            )

        print("[4/7] SHA-256 komt overeen.")

        valid, reason = database_integrity(restored)

        if not valid:
            raise RuntimeError(
                "Herstelde database is ongeldig: "
                + reason
            )

        print("[5/7] SQLite-integriteit OK.")

        restored_summary = database_summary(restored)

        if restored_summary != expected_summary:
            raise RuntimeError(
                "Tabelgegevens komen niet overeen."
            )

        print("[6/7] Alle tabellen en aantallen kloppen.")

        # Open de herstelde database echt en voer
        # een leesopdracht uit.
        with closing(
            sqlite3.connect(
                restored,
                timeout=30
            )
        ) as connection:

            tables = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()

        if not tables:
            raise RuntimeError(
                "Geen tabellen in de herstelde database."
            )

    # Controleer of de originele database
    # tijdens deze test ongewijzigd bleef.
    original_hash_after = sha256(DATABASE)

    if original_hash_before != original_hash_after:
        raise RuntimeError(
            "De originele database is tijdens de test "
            "gewijzigd. Dit kan ook komen door een "
            "gelijktijdige schrijfactie van Astra."
        )

    print("[7/7] Originele database ongewijzigd.")

    print()
    print("=" * 60)
    print("MISSIE 8 - HERSTELTEST GESLAAGD!")
    print("=" * 60)

    print(f"Gecontroleerde tabellen: {len(tables)}")
    print(f"Gebruikte back-up: {selected.name}")
    print()
    print("Je echte database is niet vervangen.")
    print("Astra's back-up kan veilig worden gelezen!")


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print()
        print("[FOUT] HERSTELTEST:")
        print(error)
        raise SystemExit(1)
