from django import forms
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from .models import UserProfile


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True, label="邮箱")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    next_page = reverse_lazy("home")

    def form_valid(self, form):
        return super().form_valid(form)

    def form_invalid(self, form):
        from life.middleware import record_login_failure, get_login_attempts
        ip = self.request.META.get("REMOTE_ADDR", "127.0.0.1")
        record_login_failure(ip)
        remaining = get_login_attempts(ip)
        if remaining == 0:
            form.add_error(None, "登录失败次数过多，请 15 分钟后再试。")
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.session.pop("login_locked", False):
            ctx["locked"] = True
        return ctx


class AccountLogoutView(LogoutView):
    next_page = reverse_lazy("login")
    http_method_names = ["get", "post", "options"]


@login_required
def export_data(request):
    fmt = request.GET.get("format", "json")
    from life.models import Expense, InstallmentPlan, Note, RecurringExpense, Reminder, Task
    from django.core.serializers import serialize
    from django.http import HttpResponse

    models = [Expense, Task, Note, Reminder, RecurringExpense, InstallmentPlan]
    user_filter = {"user": request.user}
    if fmt == "csv":
        import csv
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = "attachment; filename=lifeos-export.csv"
        w = csv.writer(resp)
        w.writerow(["type", "title", "amount", "category", "date", "status"])
        for e in Expense.objects.filter(user=request.user, is_deleted=False):
            w.writerow([e.type, e.note or e.merchant, str(e.amount), e.category.name if e.category else "", str(e.occurred_at.date()), e.status])
        for t in Task.objects.filter(user=request.user, is_deleted=False):
            w.writerow(["task", t.title, "", "", str(t.due_at.date() if t.due_at else ""), t.status])
        for n in Note.objects.filter(user=request.user):
            w.writerow(["note", n.title, "", "", str(n.occurred_on or ""), ""])
        return resp

    data = {}
    for m in [Expense, Task, Note, Reminder, RecurringExpense, InstallmentPlan]:
        qs = m.objects.filter(user=request.user)
        if hasattr(m, 'is_deleted'):
            qs = qs.filter(is_deleted=False)
        data[m.__name__] = list(qs.values())
    import json
    resp = HttpResponse(json.dumps(data, indent=2, default=str, ensure_ascii=False), content_type="application/json")
    resp["Content-Disposition"] = "attachment; filename=lifeos-export.json"
    return resp


@login_required
def delete_account(request):
    from common.audit import record
    if request.method == "POST" and request.POST.get("confirm") == "DELETE":
        user = request.user
        record(user, "login.failed", None, f"账户删除申请: {user.username}")
        # Soft anonymize: deactivate + rename
        user.is_active = False
        user.email = f"deleted_{user.pk}@archived"
        user.set_unusable_password()
        user.save()
        from django.contrib.auth import logout
        logout(request)
        from django.shortcuts import render
        return render(request, "accounts/delete_done.html")
    return render(request, "accounts/delete_confirm.html")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("display_name", "timezone", "currency", "monthly_budget", "ai_parsing_enabled", "daily_ai_limit", "email_notifications", "email_important_only")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


@login_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user.profile)
        if form.is_valid():
            form.save()
            return redirect("profile")
    else:
        form = ProfileForm(instance=request.user.profile)
    return render(request, "accounts/profile.html", {"form": form})