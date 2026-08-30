"""批量操作（P2）：账目列表多选后执行删除 / 打标签 / 改账户。

设计约束：
- 所有操作都先按 ``user=request.user`` + ``is_deleted=False`` 过滤，越权的 id 直接被过滤掉，
  不会出现「选了别人的账目」或「删了别人的数据」。
- 标签 / 账户只接受属于当前用户的对象，越权 id 静默忽略。
- 转账（transfer）类型没有 ``account`` 概念（它用 ``transfer_to_account``），批量改账户时主动排除，
  避免脏数据拖垮余额推算——与编辑页的非转账清转入逻辑一致。
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from common.audit import record

from .models import Account, Expense, Tag


def _parse_int_ids(raw):
    """把请求里的 ids / tag_ids 解析成正整数集合，非法值直接丢弃。"""
    if not isinstance(raw, (list, tuple, set)):
        return []
    out = set()
    for x in raw:
        try:
            i = int(x)
            if i > 0:
                out.add(i)
        except (TypeError, ValueError):
            pass
    return list(out)


def _scope(request, ids):
    """只返回当前用户、未删除、且 id 在范围内的账目。"""
    return Expense.objects.filter(user=request.user, is_deleted=False, pk__in=ids)


@login_required
@require_POST
def batch_expense_action(request):
    try:
        payload = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "请求格式无效。"}, status=400)

    action = payload.get("action")
    valid_actions = {"delete", "add_tag", "set_account", "clear_account"}
    if action not in valid_actions:
        return JsonResponse({"ok": False, "error": "未知操作。"}, status=400)

    ids = _parse_int_ids(payload.get("ids"))
    if not ids:
        return JsonResponse({"ok": False, "error": "请先选择账目。"}, status=400)

    qs = _scope(request, ids)

    if action == "delete":
        count = qs.count()
        if count == 0:
            return JsonResponse({"ok": False, "error": "没有可删除的账目。"}, status=400)
        qs.update(is_deleted=True, deleted_at=timezone.now())
        titles = "、".join(e.display_title for e in qs)
        record(request.user, "expense.batch_delete", 0, f"批量删除 {count} 笔账目：{titles[:120]}")
        return JsonResponse({"ok": True, "count": count, "message": f"已删除 {count} 笔账目。"})

    if action == "add_tag":
        tag_ids = _parse_int_ids(payload.get("tag_ids"))
        tags = list(Tag.objects.filter(user=request.user, pk__in=tag_ids))
        if not tags:
            return JsonResponse({"ok": False, "error": "请选择有效标签。"}, status=400)
        count = 0
        for e in qs:
            e.tags.add(*tags)  # 追加而非替换，符合「打标签」语义
            count += 1
        record(request.user, "expense.batch_tag", 0, f"批量给 {count} 笔账目添加 {len(tags)} 个标签")
        return JsonResponse({"ok": True, "count": count, "message": f"已为 {count} 笔账目添加标签。"})

    if action in ("set_account", "clear_account"):
        # 转账类型没有 account 字段，排除，避免脏数据
        target = qs.exclude(type="transfer")
        if action == "set_account":
            try:
                acc_id = int(payload.get("account_id"))
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "账户无效。"}, status=400)
            account = Account.objects.filter(user=request.user, is_deleted=False, pk=acc_id).first()
            if not account:
                return JsonResponse({"ok": False, "error": "账户不存在或已停用。"}, status=400)
            count = target.update(account=account)
            record(request.user, "expense.batch_account", account.pk, f"批量设置 {count} 笔账目账户为「{account.name}」")
            return JsonResponse({"ok": True, "count": count, "message": f"已为 {count} 笔账目设置账户「{account.name}」。"})
        # clear_account
        count = target.update(account=None)
        record(request.user, "expense.batch_account", 0, f"批量清除 {count} 笔账目的账户")
        return JsonResponse({"ok": True, "count": count, "message": f"已清除 {count} 笔账目的账户。"})
