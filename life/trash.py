"""回收站 / 撤销（P0-4）。

四类实体（Expense / Task / Note / DailyCheckin）早已具备
``is_deleted`` + ``deleted_at`` 软删除字段，但删除后无处可寻，
提示语甚至明写「不可恢复」——误删成本过高。

本模块补齐「可恢复」这一层，复用既有软删除基础设施：

- 统一的 ``kind → model`` 映射，供回收站列表 / 恢复 / 彻底删除 / 撤销共用
- 删除后生成签名 token（``django.core.signing``），目标页据此渲染「撤销」条
  （签名保证用户无法伪造 token 去恢复别人的数据）
- 过期自动清理：超过 ``TRASH_RETENTION_DAYS`` 天的软删除记录会被永久清除

设计约束：
- 只碰软删除层，不改任何记账/统计逻辑，因此不影响既有 511 条测试
- 所有查询强制 ``user=`` 过滤，杜绝跨用户越权
"""

from django.core import signing

from .models import Expense, Note, Task
from .models_daily import DailyCheckin

# 软删除记录在回收站里保留的天数，过期后自动彻底清除
TRASH_RETENTION_DAYS = 30

# 撤销 token 有效期（秒）：删除后 24 小时内可一键撤销
UNDO_TOKEN_MAX_AGE = 24 * 3600

UNDO_SALT = "life.trash.undo"
UNDO_PARAM = "undo"

# kind → (模型, 该实体「原生列表页」的 url name)
# 恢复后跳回原生列表页，符合用户「从哪删回哪去」的直觉。
TRASH_KINDS = {
    "expense": (Expense, "expense_list"),
    "task": (Task, "task_list"),
    "note": (Note, "note_list"),
    "daily": (DailyCheckin, "daily_list"),
}


def trash_model(kind):
    """返回 kind 对应的模型类；未知 kind 返回 None。"""
    entry = TRASH_KINDS.get(kind)
    return entry[0] if entry else None


def list_url_name(kind):
    """返回该实体原生列表页的 url name；未知 kind 返回 None。"""
    entry = TRASH_KINDS.get(kind)
    return entry[1] if entry else None


def kind_label(kind):
    return {
        "expense": "账目",
        "task": "任务",
        "note": "随心记",
        "daily": "每日提醒",
    }.get(kind, "条目")


def title_of(kind, obj):
    """回收站/撤销条上显示的主标题。"""
    if kind == "expense":
        return obj.display_title
    return getattr(obj, "title", None) or "未命名"


def subtitle_of(kind, obj):
    """主标题下的补充信息（金额、日期、状态等），方便用户辨认。"""
    try:
        if kind == "expense":
            sign = "+" if obj.type == "income" else "-"
            cat = obj.category.name if obj.category else "未分类"
            return f"¥{sign}{obj.amount} · {cat} · {obj.occurred_at:%Y-%m-%d}"
        if kind == "task":
            due = f"{obj.due_at:%Y-%m-%d}" if obj.due_at else "无期限"
            return f"{obj.get_status_display()} · 截止 {due}"
        if kind == "note":
            return f"{obj.occurred_on:%Y-%m-%d}" if obj.occurred_on else ""
        if kind == "daily":
            return "每日打卡提醒"
    except Exception:  # pragma: no cover — 展示层容错，不因缺字段而 500
        return ""
    return ""


# ── 撤销 token ─────────────────────────────────────────────────────
def make_undo_token(kind, pk):
    """生成「撤销」token，供删除后重定向到列表页时挂在 query 上。"""
    return signing.dumps({"k": kind, "p": pk}, salt=UNDO_SALT)


def read_undo_token(token, max_age=UNDO_TOKEN_MAX_AGE):
    """校验并解析撤销 token；无效/过期返回 None。"""
    if not token:
        return None
    try:
        data = signing.loads(token, salt=UNDO_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    except signing.SignatureExpired:
        return None
    kind, pk = data.get("k"), data.get("p")
    if kind not in TRASH_KINDS or not isinstance(pk, int):
        return None
    return kind, pk


# ── 对象获取 ───────────────────────────────────────────────────────
def get_trashed(user, kind, pk):
    """取一条「仍处在回收站中」的记录；不存在/越权/已恢复均返回 None。"""
    model = trash_model(kind)
    if model is None:
        return None
    return model.objects.filter(pk=pk, user=user, is_deleted=True).first()


def restore(user, kind, pk):
    """从回收站恢复一条记录。成功返回 (obj, True)，否则 (None, False)。"""
    obj = get_trashed(user, kind, pk)
    if obj is None:
        return None, False
    obj.is_deleted = False
    obj.deleted_at = None
    obj.save(update_fields=["is_deleted", "deleted_at"])
    return obj, True


def purge(user, kind, pk):
    """彻底删除（物理删除，不可再恢复）。成功返回 (title, True)。"""
    obj = get_trashed(user, kind, pk)
    if obj is None:
        return "", False
    title = title_of(kind, obj)
    obj.delete()
    return title, True


def collect_trash(user, per_kind=100):
    """汇总当前用户回收站里的全部条目，按删除时间倒序。

    返回 dict 列表：kind / pk / title / subtitle / deleted_at / kind_label
    """
    items = []
    for kind, (model, _url) in TRASH_KINDS.items():
        rows = model.objects.filter(user=user, is_deleted=True).order_by("-deleted_at")[:per_kind]
        for obj in rows:
            items.append({
                "kind": kind,
                "pk": obj.pk,
                "title": title_of(kind, obj),
                "subtitle": subtitle_of(kind, obj),
                "deleted_at": obj.deleted_at,
                "kind_label": kind_label(kind),
            })
    # deleted_at 可能为 None（早期软删除未回填），用 created_at 兜底无意义，
    # 这里统一把 None 排到最后，避免排序崩溃。
    items.sort(key=lambda x: (x["deleted_at"] is not None, x["deleted_at"]), reverse=True)
    return items


def undo_state_for(user, token):
    """给模板提供当前 URL 上的「可撤销」状态。

    由 context processor 调用，因此任何页面只要 URL 带着合法的 undo token
    就能渲染撤销条，无需视图逐个改造。对象已被恢复/彻底删除时返回 None。
    """
    parsed = read_undo_token(token)
    if parsed is None:
        return None
    kind, pk = parsed
    obj = get_trashed(user, kind, pk)
    if obj is None:
        return None
    return {
        "kind": kind,
        "pk": pk,
        "title": title_of(kind, obj),
        "subtitle": subtitle_of(kind, obj),
        "kind_label": kind_label(kind),
        "token": token,
    }


def purge_expired(user, retention_days=TRASH_RETENTION_DAYS):
    """惰性清理：永久删除超过保留期的软删除记录。返回清理条数。

    由回收站页 / 撤销校验时顺带触发，不引入定时任务依赖（SQLite 单用户部署）。
    """
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(days=retention_days)
    removed = 0
    for _kind, (model, _url) in TRASH_KINDS.items():
        qs = model.objects.filter(user=user, is_deleted=True, deleted_at__lt=cutoff)
        n = qs.count()
        if n:
            qs.delete()
            removed += n
    return removed
