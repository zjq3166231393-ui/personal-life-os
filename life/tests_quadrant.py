"""四象限任务视图（P2）测试：模型字段、象限分组、标记翻转、编辑保存、越权隔离。"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Task


class QuadrantTest(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("u", "u@x.com", "pw")
        self.other = User.objects.create_user("other", "o@x.com", "pw")
        self.client.login(username="u", password="pw")

    def _make(self, **kw):
        kw.setdefault("title", "任务")
        kw.setdefault("user", self.u)
        return Task.objects.create(**kw)

    # ── 模型 ──
    def test_default_flags_false(self):
        t = self._make()
        self.assertFalse(t.important)
        self.assertFalse(t.urgent)

    # ── 象限分组 ──
    def test_quadrant_grouping(self):
        self._make(title="Q1", important=True, urgent=True)
        self._make(title="Q2", important=True, urgent=False)
        self._make(title="Q3", important=False, urgent=True)
        self._make(title="Q4", important=False, urgent=False)
        # 已完成的不进矩阵
        self._make(title="done", important=True, urgent=True, status="completed")

        r = self.client.get(reverse("task_quadrant"))
        self.assertEqual(r.status_code, 200)
        quads = {q["key"]: q for q in r.context["quadrants"]}
        self.assertEqual(quads["q1"]["tasks"].count(), 1)
        self.assertEqual(quads["q2"]["tasks"].count(), 1)
        self.assertEqual(quads["q3"]["tasks"].count(), 1)
        self.assertEqual(quads["q4"]["tasks"].count(), 1)
        self.assertEqual(r.context["total"], 4)  # 不含已完成的

    # ── 翻转标记 ──
    def test_toggle_flag_flips_only_one(self):
        t = self._make(important=False, urgent=False)
        r = self.client.post(reverse("task_toggle_flag", args=[t.pk]), {"flag": "important"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertTrue(t.important)
        self.assertFalse(t.urgent)

    def test_toggle_flag_invalid_name_redirects_no_change(self):
        t = self._make(important=False, urgent=False)
        r = self.client.post(reverse("task_toggle_flag", args=[t.pk]), {"flag": "bogus"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertFalse(t.important)
        self.assertFalse(t.urgent)

    def test_toggle_flag_other_user_404(self):
        t = self._make(user=self.other, important=False)
        r = self.client.post(reverse("task_toggle_flag", args=[t.pk]), {"flag": "important"})
        self.assertEqual(r.status_code, 404)

    # ── 编辑保存 ──
    def test_edit_saves_flags(self):
        t = self._make(important=False, urgent=False)
        r = self.client.post(
            reverse("task_edit", args=[t.pk]),
            {"title": "改后", "important": "1"},  # 不带 urgent -> 应为 False
        )
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertTrue(t.important)
        self.assertFalse(t.urgent)

    # ── 登录保护 ──
    def test_login_required(self):
        self.client.logout()
        r = self.client.get(reverse("task_quadrant"))
        self.assertEqual(r.status_code, 302)
