from django.apps import AppConfig

class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "common"
    verbose_name = "通用工具"

    def ready(self):
        import common.signals  # noqa: F401
