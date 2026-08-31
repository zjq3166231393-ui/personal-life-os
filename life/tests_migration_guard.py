"""life/migration_guard.py 的单元测试。

背景：开发库（db.sqlite3）落后于代码会让页面抛 ``no such column``，表现为
「登不进去」，而测试跑在独立测试库上发现不了。这组测试锁定防护逻辑本身，
确保它"同步时静默、漂移时报警并能自动修复"。
"""

from unittest import mock

from django.test import TestCase

from .migration_guard import (
    auto_migrate_if_needed,
    format_pending,
    pending_migrations,
)


class PendingMigrationsTests(TestCase):
    """测试库在每次运行前都会完整 migrate，因此这里必须为空。"""

    def test_test_db_is_in_sync(self):
        self.assertEqual(pending_migrations(), [])

    def test_returns_sorted_list_of_tuples(self):
        pending = pending_migrations()
        self.assertIsInstance(pending, list)
        for item in pending:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)


class FormatPendingTests(TestCase):
    def test_join_app_and_name(self):
        self.assertEqual(
            format_pending([("life", "0038_x"), ("accounts", "0009_y")]),
            "life.0038_x, accounts.0009_y",
        )

    def test_empty(self):
        self.assertEqual(format_pending([]), "")


class AutoMigrateIfNeededTests(TestCase):
    def _collect(self):
        lines = []
        return lines, lines.append

    def test_silent_when_in_sync(self):
        lines, write = self._collect()
        fixed = auto_migrate_if_needed(write=write)
        self.assertEqual(fixed, 0)
        self.assertEqual(lines, [])  # 同步时不应有任何输出

    def test_reports_and_skips_when_auto_disabled(self):
        fake = [("life", "0038_x"), ("accounts", "0009_y")]
        lines, write = self._collect()
        with mock.patch(
            "life.migration_guard.pending_migrations", return_value=fake
        ):
            fixed = auto_migrate_if_needed(write=write, auto=False)

        self.assertEqual(fixed, 0)  # 未自动修复
        text = "\n".join(lines)
        self.assertIn("2 个未应用的迁移", text)
        self.assertIn("life.0038_x, accounts.0009_y", text)
        self.assertIn("跳过自动修复", text)

    def test_auto_fix_returns_fixed_count(self):
        fake = [("life", "0038_x"), ("life", "0037_y")]
        lines, write = self._collect()
        # 第一次调用返回"有漂移"，修复后第二次返回"已同步"
        with mock.patch(
            "life.migration_guard.pending_migrations", side_effect=[fake, []]
        ), mock.patch("life.migration_guard.call_command") as mocked:
            fixed = auto_migrate_if_needed(write=write, auto=True)

        self.assertEqual(fixed, 2)
        mocked.assert_called_once_with("migrate", interactive=False, verbosity=1)
        self.assertIn("已补齐", "\n".join(lines))

    def test_reports_when_auto_fix_did_not_fully_apply(self):
        fake = [("life", "0038_x")]
        lines, write = self._collect()
        with mock.patch(
            "life.migration_guard.pending_migrations", side_effect=[fake, fake]
        ), mock.patch("life.migration_guard.call_command"):
            fixed = auto_migrate_if_needed(write=write, auto=True)

        self.assertEqual(fixed, 0)
        self.assertIn("仍有 1 个未应用", "\n".join(lines))

    def test_survives_migrate_failure(self):
        """迁移失败时要给出提示，但不能把异常抛给调用方（不得阻断启动）。"""
        lines, write = self._collect()
        with mock.patch(
            "life.migration_guard.pending_migrations", return_value=[("life", "0038_x")]
        ), mock.patch(
            "life.migration_guard.call_command", side_effect=RuntimeError("boom")
        ):
            fixed = auto_migrate_if_needed(write=write, auto=True)

        self.assertEqual(fixed, 0)
        self.assertIn("自动迁移失败", "\n".join(lines))
        self.assertIn("boom", "\n".join(lines))
