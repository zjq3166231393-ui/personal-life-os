from django.urls import path
from . import views

urlpatterns = [
    path("audit-log/", views.my_audit_log, name="my_audit_log"),
]
