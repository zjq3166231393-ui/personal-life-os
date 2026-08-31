from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AuditLog, NotificationLog, PushSubscription


@login_required
def my_audit_log(request):
    logs = AuditLog.objects.filter(user=request.user)[:100]
    return render(request, "common/audit_log.html", {"logs": logs, "title": "我的操作日志"})


@login_required
def notification_list(request):
    base = NotificationLog.objects.filter(user=request.user)
    # 先聚合、后切片：切片后的 queryset 不能再 filter，否则抛
    # "Cannot filter a query once a slice has been taken"
    unread_count = base.filter(status__in=["pending", "delivered"]).count()
    notifs = base.order_by("-scheduled_at")[:50]
    return render(request, "common/notification_list.html", {
        "notifications": notifs,
        "unread_count": unread_count,
    })


@login_required
@require_POST
def notification_mark_read(request, pk):
    notif = get_object_or_404(NotificationLog, pk=pk, user=request.user)
    notif.status = "read"
    notif.read_at = timezone.now()
    notif.save()
    return redirect("notification_list")


@login_required
@require_POST
def notification_ignore(request, pk):
    notif = get_object_or_404(NotificationLog, pk=pk, user=request.user)
    notif.status = "ignored"
    notif.save()
    return redirect("notification_list")


def privacy(request):
    return render(request, "common/privacy.html")


@login_required
@require_POST
def push_subscribe(request):
    """Save browser push subscription."""
    import json
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, KeyError):
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("Invalid JSON.")

    endpoint = data.get("endpoint", "")
    if not endpoint:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("Missing endpoint.")

    keys = data.get("keys", {})
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": keys.get("p256dh", ""),
            "auth": keys.get("auth", ""),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "is_active": True,
        },
    )
    from django.http import JsonResponse
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_unsubscribe(request):
    """Deactivate a push subscription."""
    import json
    try:
        data = json.loads(request.body)
        endpoint = data.get("endpoint", "")
    except (json.JSONDecodeError, KeyError):
        endpoint = ""

    if endpoint:
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).update(is_active=False)
    else:
        PushSubscription.objects.filter(user=request.user).update(is_active=False)
    from django.http import JsonResponse
    return JsonResponse({"ok": True})


@login_required
def vapid_public_key(request):
    """Return the VAPID public key so the client can subscribe."""
    import os

    from django.http import JsonResponse
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    return JsonResponse({"publicKey": key})
