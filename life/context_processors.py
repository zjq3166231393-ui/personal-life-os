"""模板上下文处理器。

把「当前用户启用的账户」注入所有模板，供快速记账模态、账目编辑页等
需要选择账户的地方复用，避免每个视图都单独查一遍。
"""


def accounts(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from .models import Account

    return {
        "active_accounts": Account.objects.filter(
            user=request.user, is_deleted=False, is_active=True
        ).order_by("type", "name"),
    }
