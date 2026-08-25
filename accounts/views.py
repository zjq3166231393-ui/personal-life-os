from django import forms
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST

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
    """登出视图：
    - POST（推荐）：清空 session 后渲染登出过渡页（与登录页 UI 一致），
                    页面 JS 3 秒后跳转到登录页。用户也可点按钮立刻返回。
    - GET：仅从老链接进入时兼容，直接跳 login（不再渲染中间模板，避免 admin 模板干扰）。
    """
    next_page = reverse_lazy("login")
    http_method_names = ["get", "post", "options"]
    template_name = "accounts/signout_done.html"

    def post(self, request, *args, **kwargs):
        """POST 时渲染过渡页（不是立刻 redirect），3s 后客户端跳转。"""
        from django.contrib.auth import logout as auth_logout
        auth_logout(request)
        from django.shortcuts import render
        return render(request, self.template_name, status=200)

    def get(self, request, *args, **kwargs):
        """GET 兼容：直接跳 login（不渲染中间页）。"""
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(str(self.next_page))


@login_required
def export_data(request):
    """导出用户数据，支持 ?type=expense|task|note|reminder|recurring&format=csv|json
    不传 type 时导出全部（兼容老链接）。
    """
    fmt = request.GET.get("format", "json")
    type_filter = request.GET.get("type", "").strip()
    from life.models import Expense, InstallmentPlan, Note, RecurringExpense, Reminder, Task
    from django.core.serializers import serialize
    from django.http import HttpResponse

    type_to_model = {
        "expense": Expense,
        "task": Task,
        "note": Note,
        "reminder": Reminder,
        "recurring": RecurringExpense,
        "installment": InstallmentPlan,
    }
    # CSV 列定义（按 type 给出有意义的中文表头，方便 Excel/Pandas 直接打开）
    csv_columns = {
        "expense": [("type", "收支类型"), ("title", "标题/商户"), ("amount", "金额"),
                    ("category", "分类"), ("date", "发生日期"), ("status", "状态"), ("note", "备注")],
        "task":    [("title", "任务"), ("due_at", "截止时间"), ("status", "状态"),
                    ("priority", "优先级"), ("note", "备注")],
        "note":    [("title", "标题"), ("body", "正文"), ("occurred_on", "日期")],
        "reminder":[("title", "提醒"), ("due_at", "提醒时间"), ("done", "已完成")],
        "recurring":[("title", "周期账单"), ("amount", "金额"), ("frequency", "周期"),
                     ("category", "分类"), ("next_due", "下次扣款")],
        "installment":[("title", "分期"), ("total", "总额"), ("monthly", "月供"),
                       ("months_left", "剩余期数"), ("next_due", "下次扣款")],
    }

    if type_filter and type_filter in type_to_model:
        models = [type_to_model[type_filter]]
    else:
        models = [Expense, Task, Note, Reminder, RecurringExpense, InstallmentPlan]

    if fmt == "csv":
        import csv
        # BOM 让 Excel 直接认中文
        resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        type_slug = type_filter or "all"
        resp["Content-Disposition"] = f'attachment; filename="lifeos-{type_slug}.csv"'
        w = csv.writer(resp)
        if type_filter in csv_columns:
            cols = csv_columns[type_filter]
            w.writerow([label for _, label in cols])
            model = models[0]
            qs = model.objects.filter(user=request.user)
            if hasattr(model, 'is_deleted'):
                qs = qs.filter(is_deleted=False)
            # Expense 模型同时存 expense + income（type 字段区分），按 type= 过滤
            if type_filter == "expense" and hasattr(model, "type"):
                qs = qs.filter(type="expense")
            elif type_filter == "income" and hasattr(model, "type"):
                qs = qs.filter(type="income")
            for obj in qs:
                row = []
                for attr, _ in cols:
                    v = getattr(obj, attr, "")
                    if hasattr(v, 'isoformat'):
                        v = v.isoformat()
                    elif isinstance(v, bool):
                        v = "是" if v else "否"
                    row.append(v)
                w.writerow(row)
        else:
            w.writerow(["type", "title", "amount", "category", "date", "status"])
            for e in Expense.objects.filter(user=request.user, is_deleted=False):
                w.writerow([e.type, e.note or e.merchant, str(e.amount),
                            e.category.name if e.category else "",
                            str(e.occurred_at.date()), e.status])
            for t in Task.objects.filter(user=request.user, is_deleted=False):
                w.writerow(["task", t.title, "", "",
                            str(t.due_at.date() if t.due_at else ""), t.status])
            for n in Note.objects.filter(user=request.user):
                w.writerow(["note", n.title, "", "",
                            str(n.occurred_on or ""), ""])
        return resp

    data = {}
    for m in models:
        qs = m.objects.filter(user=request.user)
        if hasattr(m, 'is_deleted'):
            qs = qs.filter(is_deleted=False)
        if type_filter == "expense" and hasattr(m, "type"):
            qs = qs.filter(type="expense")
        elif type_filter == "income" and hasattr(m, "type"):
            qs = qs.filter(type="income")
        data[m.__name__] = list(qs.values())
    import json
    resp = HttpResponse(json.dumps(data, indent=2, default=str, ensure_ascii=False), content_type="application/json")
    type_slug = type_filter or "all"
    resp["Content-Disposition"] = f'attachment; filename="lifeos-{type_slug}.json"'
    return resp


@login_required
def delete_account(request):
    from common.audit import record
    from django.contrib.auth import logout
    from django.shortcuts import render
    if request.method == "POST" and request.POST.get("confirm") == "DELETE":
        user = request.user
        record(user, "account.delete", None, f"账户删除申请: {user.username}")
        user.is_active = False
        user.email = f"deleted_{user.pk}@archived"
        user.set_unusable_password()
        user.save()
        logout(request)
        return render(request, "accounts/delete_done.html")
    return render(request, "accounts/delete_confirm.html")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("display_name", "timezone", "currency", "monthly_budget",
                  "ai_parsing_enabled", "daily_ai_limit",
                  "email_notifications", "email_important_only",
                  "default_reminder_time")
        widgets = {
            "default_reminder_time": forms.TimeInput(attrs={"type": "time", "class": "lf-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


@login_required
def change_identity(request):
    """修改用户名 / 邮箱 / 手机号。各自最多可改 MAX_FIELD_CHANGES 次。
    通过 POST 字段 `field` 区分（username/email/phone）。
    """
    from django.contrib import messages
    from django.contrib.auth.models import User as AuthUser
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method != "POST":
        return redirect("profile")

    field = request.POST.get("field", "").strip()
    value = request.POST.get("value", "").strip()

    if field == "username":
        if not profile.can_change_username():
            messages.error(request, f"用户名已达到 {profile.MAX_FIELD_CHANGES} 次修改上限。")
            return redirect("profile")
        if not value or len(value) < 3 or len(value) > 30:
            messages.error(request, "用户名长度需在 3-30 之间。")
            return redirect("profile")
        if AuthUser.objects.filter(username=value).exclude(pk=request.user.pk).exists():
            messages.error(request, "该用户名已被占用。")
            return redirect("profile")
        request.user.username = value
        request.user.save(update_fields=["username"])
        profile.username_change_count += 1
        profile.save(update_fields=["username_change_count"])
        messages.success(request, f"用户名已更新（还剩 {profile.username_changes_left} 次修改机会）。")

    elif field == "email":
        if not profile.can_change_email():
            messages.error(request, f"邮箱已达到 {profile.MAX_FIELD_CHANGES} 次修改上限。")
            return redirect("profile")
        if not value or "@" not in value:
            messages.error(request, "邮箱格式无效。")
            return redirect("profile")
        if AuthUser.objects.filter(email__iexact=value).exclude(pk=request.user.pk).exists():
            messages.error(request, "该邮箱已被其他账号使用。")
            return redirect("profile")
        old_email = request.user.email
        request.user.email = value
        request.user.save(update_fields=["email"])
        profile.email_change_count += 1
        profile.save(update_fields=["email_change_count"])
        if old_email and old_email != value:
            messages.success(request, f"邮箱已从 {old_email} 改为 {value}（还剩 {profile.email_changes_left} 次修改机会）。")
        else:
            messages.success(request, f"邮箱已保存（还剩 {profile.email_changes_left} 次修改机会）。")

    elif field == "phone":
        if not profile.can_change_phone():
            messages.error(request, f"手机号已达到 {profile.MAX_FIELD_CHANGES} 次修改上限。")
            return redirect("profile")
        if value and not value.isdigit():
            messages.error(request, "手机号只能是数字。")
            return redirect("profile")
        if len(value) > 20:
            messages.error(request, "手机号过长。")
            return redirect("profile")
        old_phone = profile.phone
        profile.phone = value
        if old_phone != value:
            profile.phone_change_count += 1
        profile.save(update_fields=["phone", "phone_change_count"])
        messages.success(request, f"手机号已保存（还剩 {profile.phone_changes_left} 次修改机会）。")

    else:
        messages.error(request, "未知字段。")

    return redirect("profile")


@login_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user.profile)
        if form.is_valid():
            form.save()
            return redirect("profile")
    else:
        form = ProfileForm(instance=request.user.profile)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    # 显示名：DB 优先（profile 提交过就用 DB），否则用 username；前端 localStorage
    # 会再覆盖一层（用户在本机输入的名字），所以这里只是 SSR fallback。
    display_name = profile.display_name or request.user.username
    return render(request, "accounts/profile.html", {
        "form": form,
        "display_name": display_name,
        "profile": profile,
    })


# ── 头像上传（2026-08-24）─────────────────────────────────────────
# 限制：≤2MB，PNG/JPG/WebP。后端用 Pillow 缩放到 512x512，超长边裁剪，
# 上传成功后旧头像 URL 推入 avatar_history（上限 8 张），不删除磁盘文件。

import io
from django.core.files.base import ContentFile

AVATAR_MAX_BYTES = 2 * 1024 * 1024   # 2MB
AVATAR_MAX_SIZE = 512                # 最长边像素
AVATAR_HISTORY_MAX = 8
AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}


def _process_avatar_upload(profile, uploaded_file):
    """压缩头像到 AVATAR_MAX_SIZE，并返回 ContentFile。"""
    from PIL import Image
    raw = uploaded_file.read()
    if len(raw) > AVATAR_MAX_BYTES:
        raise ValueError(f"图片超过 {AVATAR_MAX_BYTES // 1024 // 1024}MB 限制")
    img = Image.open(io.BytesIO(raw))
    if img.format not in AVATAR_FORMATS:
        raise ValueError(f"仅支持 PNG/JPG/WebP，当前格式: {img.format}")
    img = img.convert("RGB")  # 统一 RGB，避免 RGBA/P 模式存盘异常
    img.thumbnail((AVATAR_MAX_SIZE, AVATAR_MAX_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return ContentFile(buf.getvalue(), name=f"avatar_{profile.user_id}.jpg")


@login_required
@require_POST
def avatar_upload(request):
    """上传新头像：旧头像归档到 history（最多 8 张）。"""
    from django.contrib import messages as _ms
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    f = request.FILES.get("avatar")
    if not f:
        _ms.error(request, "请选择一张图片")
        return redirect("profile")
    try:
        new_file = _process_avatar_upload(profile, f)
    except ValueError as e:
        _ms.error(request, str(e))
        return redirect("profile")
    # 把旧头像 URL 推入历史
    if profile.avatar:
        history = list(profile.avatar_history or [])
        history.append({
            "url": profile.avatar.url,
            "uploaded_at": timezone.now().isoformat(),
        })
        history = history[-AVATAR_HISTORY_MAX:]
        profile.avatar_history = history
    profile.avatar.save(new_file.name, new_file, save=True)
    _ms.success(request, "头像已更新 ✓")
    return redirect("profile")


@login_required
@require_POST
def avatar_history_select(request):
    """把历史头像里某条重新设回当前头像。"""
    from django.contrib import messages as _ms
    from django.core.files.storage import default_storage
    idx = request.POST.get("index")
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    history = list(profile.avatar_history or [])
    try:
        i = int(idx)
        if i < 0 or i >= len(history):
            raise IndexError
        chosen = history.pop(i)
    except (ValueError, IndexError):
        _ms.error(request, "无效的历史头像索引")
        return redirect("profile")
    # 当前头像也归档（如果存在且不在历史中）
    if profile.avatar and profile.avatar.url != chosen.get("url"):
        history.append({
            "url": profile.avatar.url,
            "uploaded_at": timezone.now().isoformat(),
        })
    history = history[-AVATAR_HISTORY_MAX:]
    rel_path = chosen["url"].replace(settings.MEDIA_URL, "", 1) if chosen["url"].startswith(settings.MEDIA_URL) else chosen["url"]
    try:
        with default_storage.open(rel_path, "rb") as fp:
            profile.avatar.save(f"avatar_{profile.user_id}_restored.jpg", ContentFile(fp.read()), save=True)
    except Exception as e:
        _ms.error(request, f"恢复失败：{e}")
        return redirect("profile")
    profile.avatar_history = history
    profile.save(update_fields=["avatar_history"])
    _ms.success(request, "已恢复历史头像 ✓")
    return redirect("profile")