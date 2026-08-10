from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import AuditLog


@login_required
def my_audit_log(request):
    logs = AuditLog.objects.filter(user=request.user)[:100]
    return render(request, "common/audit_log.html", {"logs": logs, "title": "我的操作日志"})


def privacy(request):
    return render(request, "common/privacy.html")
