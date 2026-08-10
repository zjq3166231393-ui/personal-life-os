from django.contrib import admin
from django.urls import include, path

from life.views_health import health

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("common/", include("common.urls")),
    path("", include("life.urls")),
]

