"""Restore database from backup.

Usage:
    python manage.py restore_db backups/backup-20260811-1200.json.gz
    python manage.py restore_db --latest          # Auto-select latest
    python manage.py restore_db --decrypt <file>  # Decrypt then restore
"""
import gzip
import os
import shutil
import sys
import time
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

BACKUP_DIR = Path("backups")


class Command(BaseCommand):
    help = "Restore database from backup."

    def add_arguments(self, parser):
        parser.add_argument("file", nargs="?", help="Backup file path")
        parser.add_argument("--latest", action="store_true", help="Restore from latest backup")
        parser.add_argument("--decrypt", action="store_true", help="Decrypt before restore")
        parser.add_argument("--dry-run", action="store_true", help="Validate without restoring")

    def handle(self, file, latest, decrypt, dry_run, **options):
        if latest or not file:
            backups = sorted(BACKUP_DIR.glob("backup-*.json.gz*"))
            if not backups:
                self.stderr.write("No backups found.")
                sys.exit(1)
            file = str(backups[-1])
            self.stdout.write(f"Using latest: {file}")

        fpath = Path(file)
        if not fpath.exists():
            self.stderr.write(f"Backup not found: {fpath}")
            sys.exit(1)

        # Decrypt if needed
        if decrypt or str(fpath).endswith(".enc"):
            key = os.getenv("BACKUP_KEY", "")
            if not key:
                self.stderr.write("BACKUP_KEY not set for decryption.")
                sys.exit(1)
            fpath = self._decrypt(fpath, key)

        if dry_run:
            self.stdout.write(f"[DRY-RUN] Would restore from {fpath}")
            return

        # Warn and confirm
        self.stdout.write(self.style.WARNING(
            "⚠ This will DELETE all current data and replace with backup."
        ))
        confirm = input(f"Type YES to confirm restore from {fpath.name}: ")
        if confirm != "YES":
            self.stdout.write("Cancelled.")
            return

        start = time.time()

        # Decompress and load
        tmp = BACKUP_DIR / "_restore_tmp.json"
        with gzip.open(fpath, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)

        # Flush + load
        call_command("flush", "--noinput")
        call_command("loaddata", str(tmp))
        tmp.unlink()

        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(
            f"Restore complete in {elapsed:.1f}s. Restart the server if running."
        ))

    def _decrypt(self, fpath, key):
        out = Path(str(fpath).replace(".enc", ""))
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import hashlib
            aes_key = hashlib.sha256(key.encode()).digest()
            aesgcm = AESGCM(aes_key)
            data = fpath.read_bytes()
            nonce, ct = data[:12], data[12:]
            pt = aesgcm.decrypt(nonce, ct, None)
            out.write_bytes(pt)
        except ImportError:
            import zipfile
            with zipfile.ZipFile(str(fpath), 'r') as zf:
                zf.extractall(path=BACKUP_DIR, pwd=key.encode())
        return out
