"""标签管理。

标签与分类是两套互补机制（详见 Tag 模型的文档字符串）：
分类用于金额统计（单选），标签用于横向检索（多值）。
因此这里只做 CRUD 与使用统计，不涉及任何金额聚合。
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Tag

# 单个用户的标签数量上限。标签的价值在于「少量、稳定、可复用」，
# 一旦泛滥就失去检索意义，因此设一个软上限。
TAG_LIMIT_PER_USER = 100


@login_required
def tag_list(request):
    tags = (
        Tag.objects.filter(user=request.user)
        .annotate(
            expense_count=Count("expenses", distinct=True),
            task_count=Count("tasks", distinct=True),
            note_count=Count("notes", distinct=True),
        )
        .order_by("name")
    )
    return render(request, "life/tag_list.html", {
        "tags": tags,
        "total": tags.count(),
        "limit": TAG_LIMIT_PER_USER,
    })


@login_required
@require_POST
def tag_create(request):
    name = (request.POST.get("name") or "").strip().lstrip("#")
    if not name:
        messages.error(request, "标签名不能为空。")
        return redirect("tag_list")
    if len(name) > 30:
        messages.error(request, "标签名请控制在 30 个字符以内。")
        return redirect("tag_list")

    if Tag.objects.filter(user=request.user, name=name).exists():
        messages.warning(request, f"标签「{name}」已存在。")
        return redirect("tag_list")

    if Tag.objects.filter(user=request.user).count() >= TAG_LIMIT_PER_USER:
        messages.error(request, f"标签数量已达上限（{TAG_LIMIT_PER_USER} 个），请先清理不用的标签。")
        return redirect("tag_list")

    Tag.objects.create(user=request.user, name=name)
    messages.success(request, f"已创建标签「{name}」")
    return redirect("tag_list")


@login_required
@require_POST
def tag_rename(request, pk):
    tag = get_object_or_404(Tag, pk=pk, user=request.user)
    name = (request.POST.get("name") or "").strip().lstrip("#")
    if not name:
        messages.error(request, "标签名不能为空。")
        return redirect("tag_list")
    if Tag.objects.filter(user=request.user, name=name).exclude(pk=tag.pk).exists():
        messages.warning(request, f"标签「{name}」已存在。")
        return redirect("tag_list")
    old = tag.name
    tag.name = name
    tag.save(update_fields=["name"])
    messages.success(request, f"已将「{old}」重命名为「{name}」")
    return redirect("tag_list")


@login_required
@require_POST
def tag_delete(request, pk):
    tag = get_object_or_404(Tag, pk=pk, user=request.user)
    name = tag.name
    # 多对多关联会由 Django 自动清理中间表，业务记录本身不受影响
    tag.delete()
    messages.success(request, f"已删除标签「{name}」（相关记录不受影响）")
    return redirect("tag_list")


# ── 供编辑页复用的辅助 ──────────────────────────────────────────────

def user_tags(user):
    """编辑页用：返回当前用户的全部标签，按名排序。"""
    return Tag.objects.filter(user=user).order_by("name")


def apply_tags(obj, user, tag_ids):
    """把提交上来的标签 id 列表应用到对象上。

    安全：只接受属于当前用户的标签 id，越权 id 会被忽略。
    """
    if tag_ids is None:
        return
    valid = Tag.objects.filter(user=user, pk__in=[t for t in tag_ids if str(t).isdigit()])
    obj.tags.set(valid)


def parse_tag_ids(post):
    """从 POST 中取出标签 id 列表（兼容多选与单个值）。"""
    ids = post.getlist("tags")
    if not ids:
        single = post.get("tags")
        ids = [single] if single else []
    return [i for i in ids if str(i).strip()]
