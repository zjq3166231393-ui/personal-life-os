"""回收站 / 撤销测试（P0-4）。

覆盖重点与项目其它测试一致：
- 正常路径：删除 → 进回收站 → 恢复 / 彻底删除 / 清空
- **越权隔离**：A 用户不能恢复或删除 B 用户的条目
- token 安全：伪造 / 过期 / 被篡改的撤销 token 一律无效
- 未登录保护：所有写操作必须登录
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Expense, Note, Task
from .models_daily import DailyCheckin
from .trash import (
    TRASH_RETENTION_DAYS,
    collect_trash,
    make_undo_token,
    purge_expired,
    read_undo_token,
    restore,
)


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _mkexpense(user, note="测试支出", amount="12.50"):
    return Expense.objects.create(
        user=user, amount=Decimal(amount), note=note,
        type="expense", status="confirmed", occurred_at=timezone.now(),
    )


class TrashRoundTripTests(TestCase):
    """删除 → 回收站 → 恢复 / 彻底删除 全链路。"""

    def setUp(self):
        self.u = _mkuser("trash_u1")
        self.client.login(username="trash_u1", password="TestPass123!")

    def test_delete_expense_lands_in_trash(self):
        e = _mkexpense(self.u)
        self.client.post(reverse("expense_delete", args=[e.pk]))
        e.refresh_from_db()
        self.assertTrue(e.is_deleted)
        self.assertIsNotNone(e.deleted_at)

        res = self.client.get(reverse("trash"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "测试支出")

    def test_trash_lists_all_four_kinds(self):
        e = _mkexpense(self.u, note="账目A")
        t = Task.objects.create(user=self.u, title="任务B")
        n = Note.objects.create(user=self.u, title="随心记C")
        d = DailyCheckin.objects.create(user=self.u, title="打卡D")
        for obj in (e, t, n, d):
            obj.is_deleted = True
            obj.deleted_at = timezone.now()
            obj.save()

        items = collect_trash(self.u)
        titles = {it["title"] for it in items}
        self.assertEqual(titles, {"账目A", "任务B", "随心记C", "打卡D"})
        self.assertEqual(len(items), 4)

    def test_restore_brings_item_back(self):
        e = _mkexpense(self.u)
        self.client.post(reverse("expense_delete", args=[e.pk]))
        self.client.post(reverse("trash_restore", args=["expense", e.pk]))
        e.refresh_from_db()
        self.assertFalse(e.is_deleted)
        self.assertIsNone(e.deleted_at)

    def test_purge_deletes_permanently(self):
        e = _mkexpense(self.u)
        self.client.post(reverse("expense_delete", args=[e.pk]))
        res = self.client.post(reverse("trash_purge", args=["expense", e.pk]))
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())
        self.assertEqual(res.status_code, 302)

    def test_empty_trash(self):
        for i in range(3):
            e = _mkexpense(self.u, note=f"条目{i}")
            self.client.post(reverse("expense_delete", args=[e.pk]))
        self.client.post(reverse("trash_empty"))
        self.assertEqual(Expense.objects.filter(user=self.u, is_deleted=True).count(), 0)

    def test_restore_missing_item_is_noop(self):
        """恢复一个不存在/已恢复的条目不应 500。"""
        res = self.client.post(reverse("trash_restore", args=["expense", 999999]))
        self.assertEqual(res.status_code, 302)

    def test_empty_trash_view_when_empty(self):
        res = self.client.get(reverse("trash"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "回收站是空的")


class UndoDeleteTests(TestCase):
    """删除后 24 小时内的撤销条。"""

    def setUp(self):
        self.u = _mkuser("undo_u1")
        self.client.login(username="undo_u1", password="TestPass123!")

    def test_delete_redirect_carries_undo_token(self):
        e = _mkexpense(self.u)
        res = self.client.post(reverse("expense_delete", args=[e.pk]))
        self.assertIn("undo=", res["Location"])

        token = res["Location"].split("undo=")[1]
        parsed = read_undo_token(token)
        self.assertEqual(parsed, ("expense", e.pk))

    def test_undo_bar_rendered_on_list_page(self):
        """带上合法 token 访问列表页时，页面应渲染撤销条 + 撤销链接。"""
        e = _mkexpense(self.u)
        res = self.client.post(reverse("expense_delete", args=[e.pk]))
        token = res["Location"].split("undo=")[1]

        res = self.client.get(reverse("expense_list") + f"?undo={token}")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "lf-undo-bar")
        self.assertContains(res, "已删除账目")

    def test_undo_restores_item(self):
        e = _mkexpense(self.u)
        res = self.client.post(reverse("expense_delete", args=[e.pk]))
        token = res["Location"].split("undo=")[1]

        res = self.client.post(reverse("trash_undo"), {"token": token})
        e.refresh_from_db()
        self.assertFalse(e.is_deleted)
        self.assertEqual(res.status_code, 302)

    def test_tampered_token_rejected(self):
        e = _mkexpense(self.u)
        self.client.post(reverse("expense_delete", args=[e.pk]))
        self.client.post(reverse("trash_undo"), {"token": "garbage.token"})
        e.refresh_from_db()
        self.assertTrue(e.is_deleted)

    def test_expired_token_rejected(self):
        kind, pk = "expense", 1
        token = make_undo_token(kind, pk)
        # max_age=0 → 立即过期
        self.assertIsNone(read_undo_token(token, max_age=0))

    def test_undo_bar_hidden_when_item_already_restored(self):
        """对象已恢复时，撤销条不应再出现。"""
        e = _mkexpense(self.u)
        res = self.client.post(reverse("expense_delete", args=[e.pk]))
        token = res["Location"].split("undo=")[1]
        restore(self.u, "expense", e.pk)

        res = self.client.get(reverse("expense_list") + f"?undo={token}")
        self.assertNotContains(res, "lf-undo-bar")


class TrashSecurityTests(TestCase):
    """越权隔离与未登录保护。"""

    def setUp(self):
        self.a = _mkuser("sec_a")
        self.b = _mkuser("sec_b")

    def _trash_both(self):
        ea = _mkexpense(self.a, note="A的账")
        eb = _mkexpense(self.b, note="B的账")
        for e in (ea, eb):
            e.is_deleted = True
            e.deleted_at = timezone.now()
            e.save()
        return ea, eb

    def test_other_users_items_not_listed(self):
        _ea, eb = self._trash_both()
        self.client.login(username="sec_a", password="TestPass123!")
        res = self.client.get(reverse("trash"))
        self.assertContains(res, "A的账")
        self.assertNotContains(res, "B的账")

    def test_cannot_restore_other_users_item(self):
        _ea, eb = self._trash_both()
        self.client.login(username="sec_a", password="TestPass123!")
        self.client.post(reverse("trash_restore", args=["expense", eb.pk]))
        eb.refresh_from_db()
        self.assertTrue(eb.is_deleted, "A 不应能恢复 B 的条目")

    def test_cannot_purge_other_users_item(self):
        _ea, eb = self._trash_both()
        self.client.login(username="sec_a", password="TestPass123!")
        self.client.post(reverse("trash_purge", args=["expense", eb.pk]))
        self.assertTrue(Expense.objects.filter(pk=eb.pk).exists(), "A 不应能彻底删除 B 的条目")

    def test_cannot_undo_with_other_users_token(self):
        """B 拿着 A 的删除 token 也不能撤销（对象按 user 过滤）。"""
        eb = _mkexpense(self.b, note="B的账")
        eb.is_deleted = True
        eb.deleted_at = timezone.now()
        eb.save()

        self.client.login(username="sec_a", password="TestPass123!")
        self.client.post(reverse("trash_undo"), {"token": make_undo_token("expense", eb.pk)})
        eb.refresh_from_db()
        self.assertTrue(eb.is_deleted)

    def test_write_actions_require_login(self):
        e = _mkexpense(self.a)
        for url in (
            reverse("trash_restore", args=["expense", e.pk]),
            reverse("trash_purge", args=["expense", e.pk]),
            reverse("trash_empty"),
            reverse("trash_undo"),
        ):
            res = self.client.post(url, {"token": "x"})
            self.assertEqual(res.status_code, 302, url)
            self.assertIn("/accounts/login", res["Location"], url)

    def test_get_not_allowed_on_write_actions(self):
        """写操作只接受 POST（GET 应被拒或重定向，不能产生副作用）。"""
        self.client.login(username="sec_a", password="TestPass123!")
        e = _mkexpense(self.a)
        e.is_deleted = True
        e.deleted_at = timezone.now()
        e.save()
        self.client.get(reverse("trash_purge", args=["expense", e.pk]))
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())


class TrashRetentionTests(TestCase):
    """过期自动清理。"""

    def setUp(self):
        self.u = _mkuser("ret_u1")

    def test_expired_items_purged(self):
        old = _mkexpense(self.u, note="很久以前")
        old.is_deleted = True
        old.deleted_at = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS + 1)
        old.save()

        fresh = _mkexpense(self.u, note="刚删的")
        fresh.is_deleted = True
        fresh.deleted_at = timezone.now()
        fresh.save()

        removed = purge_expired(self.u)
        self.assertEqual(removed, 1)
        self.assertFalse(Expense.objects.filter(pk=old.pk).exists())
        self.assertTrue(Expense.objects.filter(pk=fresh.pk).exists())

    def test_trash_view_triggers_cleanup(self):
        old = _mkexpense(self.u, note="很久以前")
        old.is_deleted = True
        old.deleted_at = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS + 1)
        old.save()

        self.client.login(username="ret_u1", password="TestPass123!")
        self.client.get(reverse("trash"))
        self.assertFalse(Expense.objects.filter(pk=old.pk).exists())
