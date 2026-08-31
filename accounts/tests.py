from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import UserProfile


class RegisterTests(TestCase):
    def test_register_page_loads(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "注册")

    def test_register_creates_user_and_redirects(self):
        response = self.client.post(reverse("register"), {
            "username": "testuser", "email": "test@example.com",
            "password1": "ComplexPass123!", "password2": "ComplexPass123!",
        })
        self.assertRedirects(response, reverse("home"))
        self.assertTrue(User.objects.filter(username="testuser").exists())

    def test_register_password_mismatch(self):
        response = self.client.post(reverse("register"), {
            "username": "testuser", "email": "test@example.com",
            "password1": "ComplexPass123!", "password2": "DifferentPass456!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="testuser").exists())

    def test_register_duplicate_username(self):
        User.objects.create_user("testuser", password="SomePass123!")
        response = self.client.post(reverse("register"), {
            "username": "testuser", "email": "another@example.com",
            "password1": "ComplexPass123!", "password2": "ComplexPass123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="testuser").count(), 1)


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("testuser", password="CorrectPass123!")

    def test_login_page_loads(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "登录")

    def test_login_success_redirects_home(self):
        response = self.client.post(reverse("login"), {
            "username": "testuser", "password": "CorrectPass123!",
        })
        self.assertRedirects(response, reverse("home"))

    def test_login_wrong_password(self):
        response = self.client.post(reverse("login"), {
            "username": "testuser", "password": "WrongPass456!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不正确")

    def test_login_nonexistent_user(self):
        response = self.client.post(reverse("login"), {
            "username": "nobody", "password": "SomePass123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不正确")


class LogoutTests(TestCase):
    def test_logout_get_redirects_to_login(self):
        """GET 登出兼容性：直接跳 login。"""
        User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.get(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

    def test_logout_post_renders_transition(self):
        """POST 登出：走我们的过渡页而不是直接 redirect。
        让用户看到「已退出登录」的视觉反馈后再跳登录，UI 与登录页一致。"""
        User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("已安全登出", body)
        self.assertIn("已退出登录", body)
        self.assertIn(str(reverse("login")), body)  # 含跳转目标 URL


class AccessControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("testuser", password="CorrectPass123!")

    def test_home_redirects_when_not_logged_in(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)

    def test_home_accessible_when_logged_in(self):
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_parse_api_redirects_when_not_logged_in(self):
        response = self.client.post(reverse("parse_entry"),
            {"text": "午餐 20 元"}, content_type="application/json")
        self.assertEqual(response.status_code, 302)

    def test_confirm_api_redirects_when_not_logged_in(self):
        response = self.client.post(reverse("confirm_actions"),
            {"actions": [{"intent": "create_note", "title": "test"}]}, content_type="application/json")
        self.assertEqual(response.status_code, 302)


class UserProfileTests(TestCase):
    def test_profile_created_on_user_creation(self):
        user = User.objects.create_user("newuser", password="Pass123!")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())

    def test_profile_created_on_register(self):
        self.client.post(reverse("register"), {
            "username": "freshuser", "email": "fresh@example.com",
            "password1": "ComplexPass123!", "password2": "ComplexPass123!",
        })
        user = User.objects.get(username="freshuser")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        profile = user.profile
        self.assertEqual(profile.timezone, "Asia/Shanghai")
        self.assertEqual(profile.currency, "CNY")
        self.assertTrue(profile.ai_parsing_enabled)

    def test_profile_page_requires_login(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)

    def test_profile_page_loads(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        # 2026-08-24 改版：个人主页合并了 profile + appearance，标题改为「我的」
        self.assertContains(response, "我的")
        # 老元素保留（账号、显示与外观、偏好、危险操作区）
        self.assertContains(response, "账号")
        self.assertContains(response, "显示与外观")
        self.assertContains(response, "偏好")
        self.assertContains(response, "账号操作")

    def test_profile_save_preferences(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.post(reverse("profile"), {
            "display_name": "小明", "timezone": "Asia/Tokyo",
            "currency": "JPY", "monthly_budget": "5000.00",
            "ai_parsing_enabled": "on", "daily_ai_limit": "10",
            "default_reminder_time": "10:00", "daily_log_reminder_time": "21:00",
        })
        self.assertRedirects(response, reverse("profile"))
        profile = user.profile
        profile.refresh_from_db()
        self.assertEqual(profile.display_name, "小明")
        self.assertEqual(profile.monthly_budget, Decimal("5000.00"))

    def test_monthly_budget_is_decimal(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        profile = user.profile
        profile.monthly_budget = Decimal("3000.50")
        profile.save()
        profile.refresh_from_db()
        self.assertIsInstance(profile.monthly_budget, Decimal)

    def test_profile_must_be_unique_per_user(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        with self.assertRaises(Exception):
            UserProfile.objects.create(user=user)

    def test_default_reminder_time_is_time_object(self):
        """TC-PROF-001 根因回归：字段默认值必须是 time 对象，不能是字符串。

        曾写成 default="10:00"：Django 只在从数据库读取时才把 TimeField 转成 time，
        而 post_save 信号里「内存中新建」的实例该字段仍是 str，
        任何 .strftime() 调用都会抛 AttributeError。
        """
        from datetime import time
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        # 刻意不 refresh_from_db：要验证的正是「内存中刚创建的实例」
        self.assertIsInstance(user.profile.default_reminder_time, time)
        self.assertEqual(user.profile.default_reminder_time.strftime("%H:%M"), "10:00")


class DataIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user("alice", password="passA")
        cls.user_b = User.objects.create_user("bob", password="passB")
        from life.models import Task
        Task.objects.create(user=cls.user_a, title="Alice 的任务", priority=1, due_at="2026-08-10T12:00:00Z")
        Task.objects.create(user=cls.user_b, title="Bob 的任务", priority=1, due_at="2026-08-10T12:00:00Z")

    def test_home_shows_only_own_data(self):
        self.client.login(username="alice", password="passA")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Alice 的任务")
        self.assertNotContains(response, "Bob 的任务")

    def test_new_entry_bound_to_current_user(self):
        self.client.login(username="alice", password="passA")
        self.client.post(reverse("confirm_actions"), {
            "actions": [{"intent": "create_note", "title": "新支出", "raw_text": "test"}],
        }, content_type="application/json")
        from life.models import Note
        note = Note.objects.filter(title="新支出").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.user_id, self.user_a.id)

    def test_user_b_cannot_see_user_a_data(self):
        self.client.login(username="bob", password="passB")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Bob 的任务")
        self.assertNotContains(response, "Alice 的任务")

    def test_unauthenticated_cannot_create_entry(self):
        response = self.client.post(reverse("confirm_actions"), {
            "actions": [{"intent": "create_note", "title": "未登录笔记"}],
        }, content_type="application/json")
        self.assertEqual(response.status_code, 302)

    def _make_resources(self, user):
        """Create one of each owned resource for `user`."""
        from life.models import Note, Reminder
        from life.models_daily import DailyCheckin
        daily = DailyCheckin.objects.create(user=user, title="A 的打卡", icon="📌")
        note = Note.objects.create(user=user, title="A 的随心记", raw_text="x")
        reminder = Reminder.objects.create(
            user=user, title="A 的提醒", reminder_type="custom",
            event_at="2026-09-01T12:00:00Z", remind_at="2026-09-01T12:00:00Z",
        )
        return daily, note, reminder

    def test_user_b_cannot_edit_or_delete_user_a_daily(self):
        from life.models_daily import DailyCheckin
        daily, _, _ = self._make_resources(self.user_a)
        self.client.login(username="bob", password="passB")
        # edit page should 404
        resp = self.client.get(reverse("daily_edit", args=[daily.pk]))
        self.assertEqual(resp.status_code, 404)
        # delete should 404 (not 302 redirect to list)
        resp = self.client.post(reverse("daily_delete", args=[daily.pk]))
        self.assertEqual(resp.status_code, 404)
        # object must still exist, untouched
        self.assertTrue(DailyCheckin.objects.filter(pk=daily.pk).exists())

    def test_user_b_cannot_delete_user_a_note(self):
        from life.models import Note
        _, note, _ = self._make_resources(self.user_a)
        self.client.login(username="bob", password="passB")
        resp = self.client.post(reverse("note_delete", args=[note.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Note.objects.filter(pk=note.pk).exists())

    def test_user_b_cannot_toggle_user_a_reminder(self):
        _, _, reminder = self._make_resources(self.user_a)
        self.client.login(username="bob", password="passB")
        resp = self.client.post(reverse("reminder_toggle", args=[reminder.pk]))
        self.assertEqual(resp.status_code, 404)
        reminder.refresh_from_db()
        self.assertTrue(reminder.is_enabled)  # unchanged

    def test_user_b_cannot_toggle_user_a_daily(self):
        daily, _, _ = self._make_resources(self.user_a)
        self.client.login(username="bob", password="passB")
        resp = self.client.post(reverse("daily_toggle", args=[daily.pk]))
        self.assertEqual(resp.status_code, 404)
        daily.refresh_from_db()
        self.assertEqual(daily.done_dates, [])  # unchanged


class ExportDataTests(TestCase):
    """导出端点的 type 过滤 + 格式（csv/json）行为（2026-08-24 增强）。"""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from life.models import Category, Expense, Task
        self.user = get_user_model().objects.create_user("alice", password="pw")
        self.client.login(username="alice", password="pw")
        cat = Category.objects.create(name="餐饮", icon="🍽️", type="expense", is_system=True)
        Expense.objects.create(user=self.user, category=cat, type="expense", amount="42",
                                note="买菜", occurred_at=timezone.now())
        Expense.objects.create(user=self.user, category=cat, type="income", amount="5000",
                                note="工资", occurred_at=timezone.now())
        Task.objects.create(user=self.user, title="买菜", priority=1, status="pending")
        Task.objects.create(user=self.user, title="交房租", priority=2, status="pending")

    def test_export_expense_csv(self):
        resp = self.client.get(reverse("export_data") + "?type=expense&format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        # BOM + 中文表头
        self.assertIn("标题", resp.content.decode("utf-8-sig"))
        # 只导出 expense 类的：工资是 income，所以只 1 条
        body = resp.content.decode("utf-8-sig")
        self.assertIn("买菜", body)
        self.assertNotIn("工资", body)
        # 文件名带 type 标识
        self.assertIn("lifeos-expense.csv", resp["Content-Disposition"])

    def test_export_task_json(self):
        resp = self.client.get(reverse("export_data") + "?type=task&format=json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/json", resp["Content-Type"])
        import json
        data = json.loads(resp.content)
        self.assertIn("Task", data)
        self.assertEqual(len(data["Task"]), 2)

    def test_export_all_legacy(self):
        """不带 type 时仍走旧路径：导出全部。"""
        resp = self.client.get(reverse("export_data") + "?format=json")
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertIn("Expense", data)
        self.assertIn("Task", data)
        self.assertIn("Note", data)

    def test_export_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("export_data") + "?type=expense&format=csv")
        self.assertEqual(resp.status_code, 302)  # 跳转登录

    def test_export_user_isolation(self):
        """B 导出时不应看到 A 的数据。"""
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user("bob", password="pwB")
        self.client.login(username="bob", password="pwB")
        resp = self.client.get(reverse("export_data") + "?type=expense&format=json")
        import json
        data = json.loads(resp.content)
        # bob 没有 Expense 记录
        self.assertEqual(len(data.get("Expense", [])), 0)


class ProfilePageIntegrationTests(TestCase):
    """2026-08-24 个人主页合并改版后，验证：
    - 渲染包含 hero、4 个区段 + 4 个导出 tile + 登出表单
    - 头像 hero 显示用户名首字母
    - 外观区段 id="appearance"（锚点可被 /appearance/ 重定向命中）
    - 危险区 form action 指向 logout
    - 注销按钮跳转 delete_account 页面
    - 老 /appearance/ URL 重定向到 /accounts/profile/#appearance（302）
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("demo", password="CorrectPass123!")
        cls.profile = cls.user.profile
        cls.profile.display_name = "小周"
        cls.profile.save()

    def setUp(self):
        self.client.login(username="demo", password="CorrectPass123!")

    def test_profile_renders_all_sections(self):
        resp = self.client.get(reverse("profile"))
        body = resp.content.decode("utf-8")
        # 区段 header
        self.assertIn("账号", body)
        self.assertIn("显示与外观", body)
        self.assertIn("偏好", body)
        self.assertIn("我的数据", body)
        self.assertIn("账号操作", body)
        # 头像 hero
        self.assertIn("lf-profile-hero", body)
        # 首字母显示
        self.assertIn(">小周</div>", body)
        # 用户名次行
        self.assertIn("@demo", body)
        # 加入日期
        self.assertIn("加入于", body)

    def test_profile_has_appearance_anchor(self):
        resp = self.client.get(reverse("profile"))
        body = resp.content.decode("utf-8")
        # 外观区段有 id，可被 /appearance/ 的 redirect 命中
        self.assertIn('id="appearance"', body)

    def test_profile_has_export_tiles(self):
        resp = self.client.get(reverse("profile"))
        body = resp.content.decode("utf-8")
        # 4 个导出 tile
        for tile_label in ["账目 CSV", "任务 CSV", "随心记 CSV", "全部 JSON"]:
            self.assertIn(tile_label, body)
        # 链接指向 /accounts/export/?type=...
        self.assertIn("/accounts/export/?type=expense", body)
        self.assertIn("/accounts/export/?type=task", body)
        self.assertIn("/accounts/export/?type=note", body)

    def test_profile_logout_form(self):
        resp = self.client.get(reverse("profile"))
        body = resp.content.decode("utf-8")
        # 登出是 POST 表单（更安全），action 指向 logout
        self.assertIn('action="/accounts/logout/"', body)
        self.assertIn("登出当前账号", body)
        # 注销按钮
        self.assertIn("/accounts/delete-account/", body)
        self.assertIn("注销账号", body)

    def test_appearance_url_redirects_to_profile(self):
        """/appearance/ 老链接 → /accounts/profile/#appearance（302）"""
        resp = self.client.get(reverse("appearance"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("profile"), resp["Location"])
        self.assertIn("#appearance", resp["Location"])

    def test_appearance_url_requires_login(self):
        """未登录访问 /appearance/ 应先去 login（@login_required）"""
        self.client.logout()
        resp = self.client.get(reverse("appearance"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_logout_renders_template(self):
        """登出后走我们自己的过渡页（而不是 Django 默认的 registration/logged_out.html）。
        2026-08-24 改为：POST 渲染过渡页（200），GET 跳 login（302）。"""
        resp = self.client.post(reverse("logout"))
        self.assertEqual(resp.status_code, 200)
        # 过渡页用我们自己的 signout_done.html
        self.assertIn("已安全登出", resp.content.decode("utf-8"))


class ProfileAvatarHomeLinkTests(TestCase):
    """2026-08-24 改造：首页右上角头像变 <a href='/accounts/profile/'> 入口"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_home_avatar_is_link(self):
        resp = self.client.get(reverse("home"))
        body = resp.content.decode("utf-8")
        # 头像变成 link 包裹（带 avatar--link 修饰类）
        self.assertIn("lf-avatar--link", body)
        # 链接指向 /accounts/profile/
        self.assertIn('href="/accounts/profile/"', body)
        self.assertIn('id="heroAvatarLink"', body)
        # 移除 hero 内冗余的「登出」文本链接
        self.assertNotIn("随时都在 ·", body)


class BottomNavMeItemTests(TestCase):
    """2026-08-24 第 6 个 tab 由「外观」改为「我的」，指向 profile。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("bob", password="passB")

    def setUp(self):
        self.client.login(username="bob", password="passB")

    def test_home_bottom_nav_last_item_is_me(self):
        resp = self.client.get(reverse("home"))
        body = resp.content.decode("utf-8")
        # 最后一项是「我的」+ /accounts/profile/
        self.assertIn('href="/accounts/profile/"', body)
        # 旧标签「外观」作为单独 tab 已不复存在（profile 内部仍保留）
        # 但 appearance URL 仍然重定向，不会出现 bottom nav 文字
        # 这里用 is-active 验证：当在 profile 页时，第六项高亮
        resp2 = self.client.get(reverse("profile"))
        body2 = resp2.content.decode("utf-8")
        # 验证 active 高亮是通过 is-active 配合 url_name=profile 触发
        # 具体 active 模板逻辑：{% if url_name == 'profile' %}is-active{% endif %}
        # 简单验证「我的」文本出现
        self.assertIn(">我的<", body2)

