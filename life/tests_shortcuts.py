"""全局快捷键：帮助弹窗与导航接入的模板测试（JS 行为由浏览器侧保证）。"""
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

User = get_user_model()


class ShortcutUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="kbuser", password="pw123456")
        self.client = Client()
        self.client.force_login(self.user)

    def test_home_includes_shortcut_help_modal(self):
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        # 帮助弹窗容器 + 标题
        self.assertContains(resp, "shortcutHelp")
        self.assertContains(resp, "键盘快捷键")
        # 快捷键处理器引用了帮助弹窗
        self.assertContains(resp, "openHelp")

    def test_sidebar_links_to_cashflow(self):
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, "/forecast/")  # 现金流侧边栏入口

    def test_help_lists_core_shortcuts(self):
        resp = self.client.get(reverse("home"))
        # 至少包含 N / S / ? / Esc 说明
        for key in ["N", "S", "?", "Esc"]:
            self.assertContains(resp, key)
