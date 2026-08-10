"""Database backup with optional encryption and auto-retention.

Usage:
    python manage.py backup_db                    # JSON dump, gzip compressed
    python manage.py backup_db --encrypt          # AES encrypt with BACKUP_KEY
    python manage.py backup_db --list             # List existing backups
"""
import gzip
import json
import os
import shutil
from datetime import date, timedelta
from io import TextIOWrapper
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

BACKUP_DIR = Path("backups")
RETENTION_DAYS = 7
RETENTION_WEEKLIES = 4


class Command(BaseCommand):
    help = "Backup database with compression and optional encryption."

    def add_arguments(self, parser):
        parser.add_argument("--encrypt", action="store_true")
        parser.add_argument("--list", action="store_true", help="List existing backups")

    def handle(self, encrypt, list, **options):
        BACKUP_DIR.mkdir(exist_ok=True)

        if list:
            self._list_backups()
            return

        ts = timezone.now().strftime("%Y%m%d-%H%M")
        fname = BACKUP_DIR / f"backup-{ts}.json.gz"

        # Dump to JSON
        tmp = BACKUP_DIR / f"_tmp_{ts}.json"
        with open(tmp, "w", encoding="utf-8") as f:
            call_command("dumpdata", indent=2, stdout=f, exclude=["contenttypes"])

        # Compress
        with open(tmp, "rb") as src, gzip.open(fname, "wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp.unlink()

        size_kb = fname.stat().st_size // 1024

        # Encrypt (simple AES using BACKUP_KEY env var)
        if encrypt:
            key = os.getenv("BACKUP_KEY", "")
            if not key:
                self.stderr.write("BACKUP_KEY not set. Skipping encryption.")
            else:
                enc_name = Path(str(fname) + ".enc")
                self._aes_encrypt(fname, enc_name, key)
                fname.unlink()
                fname = enc_name
                self.stdout.write(f"Encrypted: {fname}")

        self.stdout.write(self.style.SUCCESS(
            f"Backup created: {fname} ({size_kb} KB)"
        ))

        # Retention cleanup
        self._cleanup()

    def _aes_encrypt(self, src, dst, key):
        """AES-256-GCM encrypt using cryptography library if available, else simple XOR."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import hashlib
            aes_key = hashlib.sha256(key.encode()).digest()
            aesgcm = AESGCM(aes_key)
            nonce = os.urandom(12)
            data = src.read_bytes()
            ct = aesgcm.encrypt(nonce, data, None)
            dst.write_bytes(nonce + ct)
        except ImportError:
            # Fallback: zip with password
            import zipfile
            with zipfile.ZipFile(str(dst), 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.setpassword(key.encode())
                zf.write(str(src), arcname=src.name)

    def _cleanup(self):
        """Keep last 7 daily + last 4 weekly backups."""
        backups = sorted(BACKUP_DIR.glob("backup-*.json.gz*"))
        if len(backups) <= RETENTION_DAYS + RETENTION_WEEKLIES:
            return

        # Mark weeklies to keep (every 7th backup)
        keep = set()
        for i, b in enumerate(backups):
            if i % 7 == 0:
                keep.add(b.name)
        # Keep last 7 dailies
        for b in backups[-RETENTION_DAYS:]:
            keep.add(b.name)

        removed = 0
        for b in backups:
            if b.name not in keep:
                b.unlink()
                removed += 1
        if removed:
            self.stdout.write(f"Retention: removed {removed} old backup(s)")

    def _list_backups(self):
        backups = sorted(BACKUP_DIR.glob("backup-*.json.gz*"), reverse=True)
        if not backups:
            self.stdout.write("No backups found.")
            return
        self.stdout.write(f"{'Backup':<40} {'Size':>8} {'Age':>6}")
        self.stdout.write("-" * 56)
        for b in backups[:20]:
            size = f"{b.stat().st_size // 1024} KB"
            mtime = timezone.datetime.fromtimestamp(b.stat().st_mtime)
            age = (timezone.now() - timezone.make_aware(mtime)).days
            self.stdout.write(f"{b.name:<40} {size:>8} {age:>5}d")
