"""导航覆盖冒烟测试 — 锁定 countdown_*/recurring_* 页面底部导航/侧边栏不漏挂。

这几个页面之前没有 include life/_bottom_nav.html，也没有 body_class=lf-has-bottom-nav，
导致移动端底部导航缺失、桌面端侧边栏会盖住内容（无 padding-left:248px）。
本测试渲染这些整页并断言导航元素存在。
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from life.models import Countdown, RecurringExpense

User = get_user_model()


class NavCoverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("navcov", "navcov@example.com", "pw-nav-1234")

    def setUp(self):
        self.client.force_login(self.user)

    def _nav_ok(self, name, **kw):
        resp = self.client.get(reverse(name, kwargs=kw))
        self.assertEqual(
            resp.status_code, 200,
            f"{name} should render 200, got {resp.status_code}",
        )
        # 底部导航（移动端）与侧边栏（桌面端）都由同一个 include 输出
        self.assertContains(resp, "lf-sidebar")
        self.assertContains(resp, "lf-bottom-nav")

    def test_countdown_list_has_nav(self):
        self._nav_ok("countdown_list")

    def test_countdown_create_has_nav(self):
        self._nav_ok("countdown_create")

    def test_countdown_edit_has_nav(self):
        cd = Countdown.objects.create(
            user=self.user, title="考研", target_date=date.today()
        )
        self._nav_ok("countdown_edit", pk=cd.pk)

    def test_recurring_list_has_nav(self):
        self._nav_ok("recurring_list")

    def test_recurring_create_has_nav(self):
        self._nav_ok("recurring_create")

    def test_recurring_edit_has_nav(self):
        r = RecurringExpense.objects.create(
            user=self.user, name="房租", amount=2000, due_day=1,
            start_date=date.today(),
        )
        self._nav_ok("recurring_edit", pk=r.pk)
