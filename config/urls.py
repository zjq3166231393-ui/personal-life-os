from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import render
from django.urls import include, path

from life.views_health import health


def handler404(request, exception=None):
    return render(request, "life/error.html", {
        "title": "页面未找到", "message": "请检查链接是否正确，或返回首页。",
        "icon": "🔍", "action_url": "/", "action_label": "返回首页",
    }, status=404)


def handler500(request):
    return render(request, "life/error.html", {
        "title": "服务器错误", "message": "请稍后重试。如持续出现，请检查服务器日志。",
        "icon": "⚠", "action_url": "/", "action_label": "返回首页",
    }, status=500)


handler404 = handler404
handler500 = handler500

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("common/", include("common.urls")),
    path("", include("life.urls")),
]

# 仅 DEBUG 模式下由 Django 直接服务用户上传的头像；生产环境用 nginx/static
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

