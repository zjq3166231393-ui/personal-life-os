"""Regression tests for the v0.9.0 security / robustness hardening.

Locks in:
* safe_next() open-redirect guard (//evil.com, external host)
* countdown_pin / countdown_toggle_home now require POST (CSRF defense)
* guest account is blocked from profile / avatar-affecting views
* stored XSS: dashboard chart_data escapes </script> in user category names
* category_list counts only non-deleted expenses (N+1 refactor keeps semantics)
"""
import json
import re
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from common.utils import safe_next
from .models import Category, Countdown, Expense


class SafeNextTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _post(self, data=None):
        return self.factory.post("/", data or {})

    def test_rejects_protocol_relative(self):
        req = self._post({"next": "//evil.com"})
        self.assertEqual(safe_next(req, default="home").url, "/")

    def test_rejects_external_host(self):
        req = self._post({"next": "https://evil.com/phish"})
        self.assertEqual(safe_next(req, default="home").url, "/")

    def test_allows_internal_path(self):
        req = self._post({"next": "/countdowns/"})
        self.assertEqual(safe_next(req, default="home").url, "/countdowns/")

    def test_empty_falls_back(self):
        req = self._post({})
        self.assertEqual(safe_next(req, default="home").url, "/")


class CountdownPostOnlyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cduser", "cd@example.com", "pw123456")
        self.cd = Countdown.objects.create(
            user=self.user, title="测试", target_date=date.today() + timedelta(days=10)
        )

    def test_pin_requires_post(self):
        self.client.login(username="cduser", password="pw123456")
        resp = self.client.get(reverse("countdown_pin", args=[self.cd.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_toggle_home_requires_post(self):
        self.client.login(username="cduser", password="pw123456")
        resp = self.client.get(reverse("countdown_toggle_home", args=[self.cd.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_pin_post_changes_state(self):
        self.client.login(username="cduser", password="pw123456")
        before = self.cd.pinned
        resp = self.client.post(
            reverse("countdown_pin", args=[self.cd.pk]), {"next": "/countdowns/"}
        )
        self.assertEqual(resp.status_code, 302)
        self.cd.refresh_from_db()
        self.assertNotEqual(self.cd.pinned, before)


class GuestBlockedTests(TestCase):
    def setUp(self):
        from accounts.views import GUEST_USERNAME

        self.guest = User.objects.create_user(GUEST_USERNAME, "guest@example.com")
        self.guest.set_unusable_password()
        self.guest.save()

    def test_guest_cannot_open_profile(self):
        self.client.force_login(self.guest)
        resp = self.client.get(reverse("profile"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")


class DashboardXssEscapeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("dashuser", "d@example.com", "pw123456")
        self.cat = Category.objects.create(
            user=self.user, name='</script><script>alert(1)</script>', type="expense"
        )
        Expense.objects.create(
            user=self.user,
            category=self.cat,
            type="expense",
            amount="9.99",
            occurred_at=timezone.now(),
        )

    def test_chart_data_escapes_script_tag(self):
        self.client.login(username="dashuser", password="pw123456")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content
        # The chart-data block must not be broken by a raw </script>.
        m = re.search(
            rb'<script type="application/json" id="chart-data">(.*?)</script>',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "chart-data script block not found")
        block = m.group(1)
        self.assertNotIn(
            b"</script>", block, "raw </script> would break out of the JSON block (XSS)"
        )
        self.assertIn(b"\\u003c/script", block, "`<` must be escaped to \\u003c")
        data = json.loads(block)
        self.assertIn("</script><script>alert(1)</script>", data["catLabels"][0])


class CategoryListCountsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("catuser", "c@example.com", "pw123456")
        self.cat = Category.objects.create(user=self.user, name="餐饮", type="expense")
        Expense.objects.create(
            user=self.user, category=self.cat, type="expense", amount="10", occurred_at=timezone.now()
        )
        Expense.objects.create(
            user=self.user, category=self.cat, type="expense", amount="20", occurred_at=timezone.now()
        )
        # a deleted expense must NOT count toward the reference total
        Expense.objects.create(
            user=self.user, category=self.cat, type="expense", amount="5",
            occurred_at=timezone.now(), is_deleted=True,
        )

    def test_counts_exclude_deleted(self):
        self.client.login(username="catuser", password="pw123456")
        resp = self.client.get(reverse("category_list"))
        self.assertEqual(resp.status_code, 200)
        cats = resp.context["categories"]
        entry = next(c for c in cats if c["obj"].name == "餐饮")
        self.assertEqual(entry["refs"], 2)
