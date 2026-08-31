"""backup_db / restore_db 的端到端测试。

覆盖 ``docs/v1-release-audit.md``「缺失覆盖」中的「恢复命令端到端」。

安全说明：恢复流程的 ``flush`` + ``loaddata`` 会**清空并重写数据库**，
因此这里用 mock 接管 ``call_command``，只验证
「解压产物正确 → 调用顺序正确 → 临时文件清理」，不在测试库上真的 flush。
备份侧（``dumpdata`` 只读）则跑真实流程，验证产物是合法 gzip + JSON。
"""

import gzip
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from life.management.commands import backup_db, restore_db


def _make_backup(path: Path, payload=None) -> Path:
    """写一个 gzip 压缩的假备份文件，返回其路径。"""
    payload = payload if payload is not None else [{"model": "life.expense", "pk": 1}]
    with gzip.open(path, "wb") as fh:
        fh.write(json.dumps(payload).encode("utf-8"))
    return path


class BackupDbTests(TestCase):
    """备份命令：产物必须是合法 gzip + JSON，且不留临时文件。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_creates_valid_gzip_json(self):
        """跑真实 dumpdata（只读），验证产物可解压且是合法 JSON。"""
        with mock.patch.object(backup_db, "BACKUP_DIR", self.tmp):
            call_command("backup_db")

        files = sorted(self.tmp.glob("backup-*.json.gz"))
        self.assertEqual(len(files), 1, f"期望 1 个备份文件，实际 {files}")

        with gzip.open(files[0], "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
        self.assertIsInstance(data, list)
        # dumpdata 排除了 contenttypes，避免 loaddata 时与迁移冲突
        self.assertFalse(
            any(item.get("model") == "contenttypes.contenttype" for item in data)
        )

    def test_no_leftover_temp_file(self):
        with mock.patch.object(backup_db, "BACKUP_DIR", self.tmp):
            call_command("backup_db")
        self.assertEqual(list(self.tmp.glob("_tmp_*.json")), [])

    def test_list_with_no_backups(self):
        with mock.patch.object(backup_db, "BACKUP_DIR", self.tmp):
            call_command("backup_db", "--list")

    def test_encrypt_requires_key(self):
        with mock.patch.object(backup_db, "BACKUP_DIR", self.tmp):
            with mock.patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("BACKUP_KEY", None)
                with self.assertRaises(SystemExit):
                    call_command("backup_db", "--encrypt")
        # 失败时应清理掉已生成的明文备份
        self.assertEqual(list(self.tmp.glob("backup-*.json.gz")), [])


class RestoreDbTests(TestCase):
    """恢复命令：参数校验、确认流程、调用顺序与临时文件清理。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _restore(self, *args, confirm=None, **kwargs):
        with mock.patch.object(restore_db, "BACKUP_DIR", self.tmp):
            with mock.patch.object(restore_db, "call_command") as mocked:
                if confirm is not None:
                    with mock.patch("builtins.input", return_value=confirm):
                        call_command("restore_db", *args, **kwargs)
                else:
                    call_command("restore_db", *args, **kwargs)
                return mocked

    def test_missing_file_exits(self):
        with mock.patch.object(restore_db, "BACKUP_DIR", self.tmp):
            with self.assertRaises(SystemExit):
                call_command("restore_db", "does-not-exist.json.gz")

    def test_latest_without_backups_exits(self):
        with mock.patch.object(restore_db, "BACKUP_DIR", self.tmp):
            with self.assertRaises(SystemExit):
                call_command("restore_db", "--latest")

    def test_dry_run_does_not_touch_database(self):
        """--dry-run 只校验，不 flush、不要求交互确认。"""
        backup = _make_backup(self.tmp / "backup-20260101-0000.json.gz")
        mocked = self._restore(str(backup), dry_run=True)
        mocked.assert_not_called()

    def test_cancelled_when_user_does_not_confirm(self):
        backup = _make_backup(self.tmp / "backup-20260101-0000.json.gz")
        mocked = self._restore(str(backup), confirm="no")
        mocked.assert_not_called()

    def test_confirm_restores_in_correct_order(self):
        backup = _make_backup(self.tmp / "backup-20260101-0000.json.gz")
        mocked = self._restore(str(backup), confirm="YES")

        # 必须先 flush 再 loaddata，顺序错了会导致主键冲突
        calls = [c.args[0] for c in mocked.call_args_list]
        self.assertEqual(calls, ["flush", "loaddata"])

    def test_decompressed_payload_is_valid_json(self):
        """解压出来的临时文件必须是合法 JSON，且内容与备份一致。"""
        payload = [{"model": "life.expense", "pk": 7, "fields": {"amount": "12.34"}}]
        backup = _make_backup(self.tmp / "backup-20260101-0000.json.gz", payload)

        captured = {}

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                captured["data"] = json.loads(Path(args[0]).read_text(encoding="utf-8"))
            return None

        with mock.patch.object(restore_db, "BACKUP_DIR", self.tmp):
            with mock.patch.object(restore_db, "call_command", side_effect=fake_call_command):
                with mock.patch("builtins.input", return_value="YES"):
                    call_command("restore_db", str(backup))

        self.assertEqual(captured["data"], payload)

    def test_temp_file_cleaned_up_after_restore(self):
        backup = _make_backup(self.tmp / "backup-20260101-0000.json.gz")
        self._restore(str(backup), confirm="YES")
        self.assertFalse((self.tmp / "_restore_tmp.json").exists())

    def test_latest_picks_most_recent_backup(self):
        _make_backup(self.tmp / "backup-20260101-0000.json.gz")
        _make_backup(self.tmp / "backup-20260202-0000.json.gz")
        mocked = self._restore("--latest", confirm="YES")
        self.assertEqual([c.args[0] for c in mocked.call_args_list], ["flush", "loaddata"])
