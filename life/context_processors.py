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


def undo_state(request):
    """把 URL 上携带的「撤销 token」解析成模板可用的 undo_item。

    删除类视图删除成功后会重定向到列表页并挂上 ``?undo=<signed>``，
    这里统一解析，_base.html 据此渲染撤销条——无需每个视图单独处理。
    非登录用户 / 无 token / token 失效 → 返回空，模板侧自动不渲染。
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from .trash import UNDO_PARAM, undo_state_for

    item = undo_state_for(request.user, request.GET.get(UNDO_PARAM, ""))
    return {"undo_item": item} if item else {}
