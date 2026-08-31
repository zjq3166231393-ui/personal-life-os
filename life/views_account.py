"""账户管理。

账户与分类正交：分类回答「钱花在哪」（支出结构），账户回答「钱从哪出」（资金分布与余额）。

删除策略：软删除 + 关联流水的 account 置空（on_delete=SET_NULL）。
即删账户不会删流水，历史账目仍在，只是「未指定账户」——避免误删导致数据丢失。
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Account, Expense

ACCOUNT_LIMIT_PER_USER = 50


@login_required
def account_list(request):
    accounts = list(Account.objects.filter(user=request.user, is_deleted=False))
    ids = [a.id for a in accounts]
    zero = Decimal("0")
    # ── 余额批量聚合：替代 Account.balance 每账户 4 次查询的 N+1 ──
    # 旧实现遍历每个账户调用 account.balance（income/expense/转出/转入 4 次聚合），
    # K 个账户 = 4K 次查询；改为 2 次 GROUP BY 一次算完所有账户余额。
    out_rows = (
        Expense.objects.filter(
            user=request.user, is_deleted=False, status="confirmed", account_id__in=ids,
        )
        .values("account_id")
        .annotate(
            income=Sum("amount", filter=Q(type="income")),
            expense=Sum("amount", filter=Q(type="expense")),
            out_xfer=Sum("amount", filter=Q(type="transfer")),
        )
    )
    out_map = {r["account_id"]: r for r in out_rows}
    in_rows = (
        Expense.objects.filter(
            user=request.user, is_deleted=False, status="confirmed", transfer_to_account_id__in=ids,
        )
        .values("transfer_to_account_id")
        .annotate(in_xfer=Sum("amount", filter=Q(type="transfer")))
    )
    in_map = {r["transfer_to_account_id"]: (r["in_xfer"] or zero) for r in in_rows}

    rows = []
    total = Decimal("0")
    for a in accounts:
        o = out_map.get(a.id, {})
        bal = (
            a.initial_balance
            + (o.get("income") or zero)
            - (o.get("expense") or zero)
            - (o.get("out_xfer") or zero)
            + in_map.get(a.id, zero)
        )
        rows.append({"obj": a, "balance": bal})
        total += bal
    return render(request, "life/account_list.html", {
        "rows": rows,
        "total": total,
        "types": Account.Type.choices,
        "limit": ACCOUNT_LIMIT_PER_USER,
        "count": len(accounts),
    })


@login_required
@require_POST
def account_create(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "账户名不能为空。")
        return redirect("account_list")

    type_ = request.POST.get("type", "cash")
    if type_ not in dict(Account.Type.choices):
        type_ = "cash"

    if Account.objects.filter(user=request.user, name=name).exists():
        messages.warning(request, f"账户「{name}」已存在。")
        return redirect("account_list")

    if Account.objects.filter(user=request.user, is_deleted=False).count() >= ACCOUNT_LIMIT_PER_USER:
        messages.error(request, f"账户数量已达上限（{ACCOUNT_LIMIT_PER_USER} 个）。")
        return redirect("account_list")

    Account.objects.create(
        user=request.user,
        name=name[:50],
        type=type_,
        initial_balance=_parse_amount(request.POST.get("initial_balance")),
    )
    messages.success(request, f"已创建账户「{name}」")
    return redirect("account_list")


@login_required
def account_edit(request, pk):
    account = get_object_or_404(Account, pk=pk, user=request.user, is_deleted=False)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "账户名不能为空。")
            return redirect("account_edit", pk=pk)
        if Account.objects.filter(user=request.user, name=name).exclude(pk=account.pk).exists():
            messages.warning(request, f"账户「{name}」已存在。")
            return redirect("account_edit", pk=pk)
        type_ = request.POST.get("type", account.type)
        if type_ not in dict(Account.Type.choices):
            type_ = account.type
        account.name = name[:50]
        account.type = type_
        account.icon = (request.POST.get("icon") or "").strip()[:8]
        account.initial_balance = _parse_amount(request.POST.get("initial_balance"), account.initial_balance)
        account.is_active = request.POST.get("is_active") == "on"
        account.save()
        messages.success(request, f"已保存账户「{account.name}」")
        return redirect("account_list")
    return render(request, "life/account_edit.html", {
        "account": account,
        "types": Account.Type.choices,
        "balance": account.balance,
    })


@login_required
@require_POST
def account_delete(request, pk):
    account = get_object_or_404(Account, pk=pk, user=request.user, is_deleted=False)
    name = account.name
    account.is_deleted = True
    account.deleted_at = timezone.now()
    account.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    # 流水的 account 由 on_delete=SET_NULL 置空，历史记录完整保留
    messages.success(request, f"已删除账户「{name}」，相关流水保留（账户显示为未指定）")
    return redirect("account_list")


@login_required
def account_detail(request, pk):
    """单个账户的流水明细。"""
    account = get_object_or_404(Account, pk=pk, user=request.user, is_deleted=False)
    # 转出与转入都算这个账户的流水
    items = (
        Expense.objects.filter(is_deleted=False)
        .filter(Q(account=account) | Q(transfer_to_account=account))
        .select_related("category", "account", "transfer_to_account")
        .order_by("-occurred_at")[:200]
    )
    return render(request, "life/account_detail.html", {
        "account": account,
        "items": items,
        "balance": account.balance,
    })


def _parse_amount(raw, fallback=Decimal("0.00")):
    """把表单金额解析为 Decimal；非法或为负时回退。

    初始余额允许负数吗？信用卡欠款场景下有意义，但会让「总资产」口径混乱，
    这里统一按 0 处理非法值，负数保留（用户可能确实用负初值表示欠款）。
    """
    if raw is None or str(raw).strip() == "":
        return fallback
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return fallback
