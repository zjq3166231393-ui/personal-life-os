from django.contrib.auth.signals import user_login_failed
from django.dispatch import receiver

from .audit import record


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    username = credentials.get("username", "?")
    record(user=None, action="login.failed", summary=f"用户名: {str(username)[:150]}")
