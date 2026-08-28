from django.urls import path

from . import views

urlpatterns = [
    path("audit-log/", views.my_audit_log, name="my_audit_log"),
    path("privacy/", views.privacy, name="privacy"),
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/<int:pk>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("notifications/<int:pk>/ignore/", views.notification_ignore, name="notification_ignore"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    path("push/vapid-public-key/", views.vapid_public_key, name="vapid_public_key"),
]
