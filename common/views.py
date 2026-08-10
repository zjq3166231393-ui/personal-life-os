from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import AuditLog, NotificationLog


@login_required
def my_audit_log(request):
    logs = AuditLog.objects.filter(user=request.user)[:100]
    return render(request, "common/audit_log.html", {"logs": logs, "title": "我的操作日志"})


@login_required
def notification_list(request):
    notifs = NotificationLog.objects.filter(user=request.user).order_by(
        "-scheduled_at"
    )[:50]
    unread_count = notifs.filter(status__in=["pending", "delivered"]).count()
    return render(request, "common/notification_list.html", {
        "notifications": notifs,
        "unread_count": unread_count,
    })


@login_required
def notification_mark_read(request, pk):
    notif = get_object_or_404(NotificationLog, pk=pk, user=request.user)
    notif.status = "read"
    notif.read_at = timezone.now()
    notif.save()
    return redirect("notification_list")


@login_required
def notification_ignore(request, pk):
    notif = get_object_or_404(NotificationLog, pk=pk, user=request.user)
    notif.status = "ignored"
    notif.save()
    return redirect("notification_list")


def privacy(request):
    return render(request, "common/privacy.html")
