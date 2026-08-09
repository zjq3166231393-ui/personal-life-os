from django.db import models
from django.conf import settings


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    display_name = models.CharField(max_length=50, blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Shanghai")
    currency = models.CharField(max_length=8, default="CNY")
    monthly_budget = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    ai_parsing_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_user_profile"

    def __str__(self):
        return f"Profile({self.user.username})"
