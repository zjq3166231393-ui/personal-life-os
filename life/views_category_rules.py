"""自动分类规则管理 + 实时建议接口。

列表 / 新建 / 编辑 / 删除走标准 Django 视图（与 recurring_create/edit 一样直接读 POST，
不引入 ModelForm，保持项目既有风格）；/api/suggest-category/ 供首页快速记账面板在用户
输入备注时实时预选分类。
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .category_rules import match_category
from .models import Category, CategoryRule


@login_required
def category_rule_list(request):
    rules = CategoryRule.objects.filter(user=request.user).select_related("category")
    return render(request, "life/category_rules.html", {"rules": rules})


def _category_choices(user):
    return Category.objects.filter(
        Q(user=user) | Q(user__isnull=True), is_active=True
    ).order_by("type", "name")


@login_required
def category_rule_create(request):
    categories = _category_choices(request.user)
    if request.method == "POST":
        rule = _save_rule(request, CategoryRule(user=request.user), categories)
        if rule is not None:
            return redirect("category_rule_list")
    return render(
        request, "life/category_rule_form.html",
        {"categories": categories, "is_edit": False, "rule": None},
    )


@login_required
def category_rule_edit(request, pk):
    rule = get_object_or_404(CategoryRule, pk=pk, user=request.user)
    categories = _category_choices(request.user)
    if request.method == "POST":
        updated = _save_rule(request, rule, categories)
        if updated is not None:
            return redirect("category_rule_list")
    return render(
        request, "life/category_rule_form.html",
        {"categories": categories, "is_edit": True, "rule": rule},
    )


def _save_rule(request, rule, categories):
    """从 POST 构造/更新一条规则，校验通过返回保存后的实例，否则返回 None。"""
    pattern = (request.POST.get("pattern") or "").strip()
    cat_id = request.POST.get("category")
    type_filter = request.POST.get("type_filter", CategoryRule.TypeFilter.BOTH)
    priority = request.POST.get("priority", "0").strip()
    is_active = request.POST.get("is_active") == "on"

    if not pattern:
        return None
    if cat_id:
        category = categories.filter(pk=cat_id).first()
    else:
        category = None
    if category is None:
        return None
    if type_filter not in dict(CategoryRule.TypeFilter.choices):
        type_filter = CategoryRule.TypeFilter.BOTH
    try:
        priority_val = int(priority)
    except (TypeError, ValueError):
        priority_val = 0
    if priority_val < 0:
        priority_val = 0
    if priority_val > 999:
        priority_val = 999

    rule.pattern = pattern[:100]
    rule.category = category
    rule.type_filter = type_filter
    rule.priority = priority_val
    rule.is_active = is_active
    rule.save()
    return rule


@login_required
@require_POST
def category_rule_delete(request, pk):
    rule = get_object_or_404(CategoryRule, pk=pk, user=request.user)
    rule.delete()
    return redirect("category_rule_list")


@login_required
def suggest_category(request):
    """实时建议：给定文本与类型，返回规则命中的分类（或 null）。

    仅作「建议」，绝不强制——用户手动选了就以用户的为准。
    """
    q = (request.GET.get("q") or "").strip()
    type_ = request.GET.get("type", "expense")
    cat = match_category(request.user, q, type_) if q else None
    if cat is None:
        return JsonResponse({"ok": True, "category_id": None, "category_name": None})
    return JsonResponse(
        {"ok": True, "category_id": cat.id, "category_name": cat.name}
    )
