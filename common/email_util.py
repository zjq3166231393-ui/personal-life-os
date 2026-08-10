"""Simple email notification utility. Falls back silently if SMTP unconfigured."""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_notification_email(user, title, body, retry_count=0):
    """Send a notification email. Never includes amount/category details.
    Returns (success: bool, error_msg: str).
    """
    if not user.email:
        return False, "No email address."

    if not hasattr(user, 'profile') or not user.profile.email_notifications:
        return False, "Email notifications disabled."

    try:
        subject = f"[Life OS] {title}"
        # Only title + time, never amount or category
        plain = f"{title}\n\n{body}\n\n— Personal Life OS"
        send_mail(
            subject=subject,
            message=plain,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True, ""
    except Exception as e:
        err = str(e)[:500]
        logger.warning(f"Email failed for {user.username}: {err}")
        return False, err
