"""回收站 / 撤销视图（P0-4）。

对外暴露 5 个动作：
- ``trash_list``   回收站总览（跨账目/任务/随心记/每日提醒）
- ``trash_restore`` 恢复单条
- ``trash_purge``   彻底删除单条
- ``trash_empty``   清空回收站
- ``undo_delete``   删除后 24 小时内的「撤销」按钮

所有动作强制 ``user=`` 过滤 + ``@require_POST``，幂等且不可越权。
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from common.audit import record

from .trash import (
    TRASH_RETENTION_DAYS,
    collect_trash,
    list_url_name,
    purge,
    purge_expired,
    read_undo_token,
    restore,
    title_of,
)


@login_required
def trash_list(request):
    """回收站总览：按删除时间倒序列出可恢复条目。"""
    # 惰性清理过期条目（超过保留期的软删除记录永久删除）
    expired = purge_expired(request.user)

    items = collect_trash(request.user)
    counts = {}
    for it in items:
        row = counts.setdefault(it["kind"], {"label": it["kind_label"], "n": 0})
        row["n"] += 1

    return render(request, "life/trash.html", {
        "items": items,
        "counts": counts,
        "total": len(items),
        "retention_days": TRASH_RETENTION_DAYS,
        "expired_cleaned": expired,
    })


@login_required
@require_POST
def trash_restore(request, kind, pk):
    """恢复单条记录，成功后跳回该实体的原生列表页。"""
    obj, ok = restore(request.user, kind, pk)
    if ok:
        record(request.user, f"trash.restore.{kind}", pk, f"从回收站恢复: {title_of(kind, obj)}")
        messages.success(request, f"已恢复「{title_of(kind, obj)}」")
    else:
        messages.error(request, "该条目已不在回收站中。")
    return redirect(request.POST.get("next") or "trash")


@login_required
@require_POST
def trash_purge(request, kind, pk):
    """彻底删除单条（物理删除，不可恢复）。二次确认在模板侧完成。"""
    title, ok = purge(request.user, kind, pk)
    if ok:
        record(request.user, f"trash.purge.{kind}", pk, f"彻底删除: {title}")
        messages.success(request, f"已彻底删除「{title}」")
    else:
        messages.error(request, "该条目已不在回收站中。")
    return redirect(request.POST.get("next") or "trash")


@login_required
@require_POST
def trash_empty(request):
    """清空回收站：永久删除当前用户所有软删除条目。"""
    n = 0
    for it in collect_trash(request.user, per_kind=1000):
        _title, ok = purge(request.user, it["kind"], it["pk"])
        n += 1 if ok else 0
    if n:
        record(request.user, "trash.empty", 0, f"清空回收站：{n} 条")
        messages.success(request, f"已清空回收站（{n} 条已永久删除）")
    else:
        messages.info(request, "回收站本来就是空的。")
    return redirect("trash")


@login_required
@require_POST
def undo_delete(request):
    """删除后的「撤销」按钮入口。

    token 由 ``trash.make_undo_token`` 签发（24h 有效），
    页面把它放在隐藏字段里，这里校验后恢复对象并跳回原生列表页。
    """
    token = request.POST.get("token", "")
    parsed = read_undo_token(token)
    if parsed is None:
        messages.error(request, "撤销链接已失效（超过 24 小时），可在回收站手动恢复。")
        return redirect("trash")

    kind, pk = parsed
    obj, ok = restore(request.user, kind, pk)
    if ok:
        record(request.user, f"trash.undo.{kind}", pk, f"撤销删除: {title_of(kind, obj)}")
        messages.success(request, f"已撤销删除「{title_of(kind, obj)}」")
        url_name = list_url_name(kind)
        if url_name:
            return redirect(url_name)
    else:
        messages.error(request, "该条目已不在回收站中，可能已被恢复或彻底删除。")
    return redirect("trash")


def undo_redirect(view_name, kind, pk, token=None):
    """删除成功后重定向到列表页，并挂上撤销 token。

    用法：::

        return undo_redirect("expense_list", "expense", expense.pk)
    """
    from .trash import make_undo_token

    url = reverse(view_name)
    tok = token or make_undo_token(kind, pk)
    sep = "&" if "?" in url else "?"
    return redirect(f"{url}{sep}undo={tok}")
