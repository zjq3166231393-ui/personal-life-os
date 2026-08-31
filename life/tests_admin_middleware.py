"""Admin 访问控制中间件回归测试。

锁定 `life.middleware.AdminAccessMiddleware` 的契约：
* 默认（无环境变量）完全放行，本地开发不受影响；
* `ADMIN_ENABLED=false` 时 `/admin/` 返回 404（而非 403，不泄露路径存在）；
* `ADMIN_ALLOWED_IPS` 白名单：命中放行、未命中 404；
* 非 `/admin/` 路径永远放行，即使开了白名单。

env 在请求时读取，测试用 `with patch.dict(os.environ, ...)` 隔离。
"""
import os
from unittest.mock import patch

from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase

from life.middleware import AdminAccessMiddleware


def _pass_response(request):
    return HttpResponse("ok", status=200)


class AdminAccessMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get(self, path, ip="127.0.0.1"):
        req = self.factory.get(path)
        req.META["REMOTE_ADDR"] = ip
        return AdminAccessMiddleware(_pass_response)(req)

    def test_default_passes_admin_through(self):
        # 不设置任何环境变量 → 行为与以前一致，/admin/ 放行
        with patch.dict(os.environ, {}, clear=True):
            resp = self._get("/admin/")
        self.assertEqual(resp.status_code, 200)

    def test_non_admin_path_always_passes(self):
        with patch.dict(os.environ, {"ADMIN_ENABLED": "false"}):
            resp = self._get("/dashboard/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_disabled_returns_404(self):
        with patch.dict(os.environ, {"ADMIN_ENABLED": "false"}):
            with self.assertRaises(Http404):
                self._get("/admin/")

    def test_admin_disabled_returns_404_case_insensitive(self):
        with patch.dict(os.environ, {"ADMIN_ENABLED": "FALSE"}):
            with self.assertRaises(Http404):
                self._get("/admin/auth/user/")

    def test_allowed_ips_pass_when_whitelisted(self):
        with patch.dict(os.environ, {"ADMIN_ALLOWED_IPS": "10.0.0.5,127.0.0.1"}):
            resp = self._get("/admin/", ip="127.0.0.1")
        self.assertEqual(resp.status_code, 200)

    def test_allowed_ips_404_when_not_whitelisted(self):
        with patch.dict(os.environ, {"ADMIN_ALLOWED_IPS": "10.0.0.5,192.168.1.9"}):
            with self.assertRaises(Http404):
                self._get("/admin/", ip="203.0.113.7")

    def test_trust_proxy_uses_x_forwarded_for(self):
        # 开启反代信任后，取 XFF 首段 IP
        with patch.dict(
            os.environ,
            {"ADMIN_ALLOWED_IPS": "203.0.113.7", "ADMIN_TRUST_PROXY": "true"},
        ):
            req = self.factory.get("/admin/")
            req.META["REMOTE_ADDR"] = "10.0.0.99"
            req.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.7, 10.0.0.99"
            resp = AdminAccessMiddleware(_pass_response)(req)
        self.assertEqual(resp.status_code, 200)
