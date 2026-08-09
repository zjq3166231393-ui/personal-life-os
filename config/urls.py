from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("finance/", include("finance.urls")),
    path("planning/", include("planning.urls")),
    path("notes/", include("notes.urls")),
    path("capture/", include("capture.urls")),
    path("common/", include("common.urls")),
    path("", include("life.urls")),
]

