from .models import AuditLog


def record(user, action, target_id=None, summary=""):
    user_id = user.pk if user is not None and hasattr(user, 'pk') else None
    AuditLog.objects.create(user_id=user_id, action=action, target_id=target_id, summary=str(summary)[:500])
