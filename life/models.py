import uuid as _uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone  # noqa: F401 — receipt_upload_to() 用

from .currency import CURRENCY_CHOICES


class CategoryRule(models.Model):
    """自动分类规则：商户名/备注包含某关键字时，自动归入指定分类。

    解决「重复劳动」痛点——同一家店（星巴克、滴滴、盒马）每次都得手动选分类。
    规则按 priority 降序匹配，命中第一条即返回，便于用户用优先级控制冲突。
    """

    class TypeFilter(models.TextChoices):
        EXPENSE = "expense", "支出"
        INCOME = "income", "收入"
        BOTH = "both", "全部"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="category_rules"
    )
    pattern = models.CharField(
        max_length=100, help_text="商户名/备注包含的关键字（不区分大小写）"
    )
    category = models.ForeignKey(
        "Category", on_delete=models.CASCADE, related_name="auto_rules"
    )
    type_filter = models.CharField(
        max_length=10, choices=TypeFilter.choices, default=TypeFilter.BOTH
    )
    priority = models.PositiveSmallIntegerField(
        default=0, help_text="数值越大越优先匹配；相同则按创建顺序"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]

    def __str__(self):
        return f"「{self.pattern}」→ {self.category.name}"


class Category(models.Model):
    """Expense category with system defaults and per-user customization."""

    class Type(models.TextChoices):
        EXPENSE = "expense", "支出"
        INCOME = "income", "收入"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories", null=True, blank=True, help_text="NULL = system default category")
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=8, blank=True)
    type = models.CharField(max_length=20, choices=Type.choices, default="expense")
    color = models.CharField(max_length=20, blank=True, help_text="Tailwind or hex color, e.g. #f97316 or orange-500")
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["type", "name"]
        constraints = [models.UniqueConstraint(fields=["user", "name", "type"], name="unique_category_per_user")]

    def __str__(self):
        return f"{self.icon or ''} {self.name}"


class Expense(models.Model):
    class TransactionType(models.TextChoices):
        EXPENSE = "expense", "支出"
        INCOME = "income", "收入"
        TRANSFER = "transfer", "转账"

    class Source(models.TextChoices):
        VOICE = "voice", "语音"
        TEXT = "text", "文本"
        MANUAL = "manual", "手动"
        RECURRING = "recurring", "周期"
        AI = "ai", "AI"

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "已确认"
        PENDING = "pending", "待确认"
        VOIDED = "voided", "已作废"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expenses")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    type = models.CharField(max_length=20, choices=TransactionType.choices, default="expense")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    occurred_at = models.DateTimeField()
    merchant = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=500, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default="manual")
    status = models.CharField(max_length=20, choices=Status.choices, default="confirmed")
    raw_text = models.TextField(blank=True)
    tags = models.ManyToManyField("Tag", blank=True, related_name="expenses")
    account = models.ForeignKey(
        "Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transactions",
        help_text="资金变动的账户：支出时从该账户扣减，收入时计入该账户",
    )
    transfer_to_account = models.ForeignKey(
        "Account", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="incoming_transfers",
        help_text="仅 type=transfer 时有值：转入的目标账户",
    )
    # ── 多币种（P1-5）─────────────────────────────────────────
    currency = models.CharField(
        max_length=3, choices=CURRENCY_CHOICES, default="CNY",
        help_text="记账币种；本位币为 CNY，非本位币可填汇率折算",
    )
    rate = models.DecimalField(
        max_digits=12, decimal_places=6, default=Decimal("1"),
        help_text="1 单位本币 = rate 单位本位币（CNY）；手工填写，默认 1",
    )
    amount_base = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="折算后的本位币金额（保存时自动计算）",
    )
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            # 列表/看板高频：filter(user, is_deleted=False, occurred_at__range)
            models.Index(fields=["user", "is_deleted", "occurred_at"]),
            # 类型/状态筛选 + 时间排序
            models.Index(fields=["user", "is_deleted", "type", "status"]),
            # 按分类聚合（预算/分析页 values("category").annotate(Sum)）
            models.Index(fields=["user", "is_deleted", "category"]),
        ]

    @property
    def display_title(self):
        """给这笔账起个最合适的名字：备注 > 商家 > 分类名 > 未命名。"""
        return self.note or self.merchant or (self.category.name if self.category else None) or "未命名"

    def save(self, *args, **kwargs):
        """保存时自动折算本位币金额 amount_base（统一 2 位小数）。"""
        from .currency import BASE_CURRENCY, to_base

        # 兼容编辑页直接赋值字符串金额：先转 Decimal
        amt = self.amount
        if not isinstance(amt, Decimal):
            try:
                amt = Decimal(str(amt))
            except (TypeError, ValueError):
                amt = Decimal("0")

        if self.currency == BASE_CURRENCY:
            self.rate = Decimal("1")
            self.amount_base = amt.quantize(Decimal("0.01"))
        else:
            base = to_base(amt, self.currency, self.rate)
            self.amount_base = base.quantize(Decimal("0.01")) if base is not None else None
        super().save(*args, **kwargs)

    @property
    def is_foreign_currency(self):
        from .currency import BASE_CURRENCY

        return self.currency not in (BASE_CURRENCY, None, "")

    @property
    def display_amount(self):
        """带币种符号的展示金额，如 ¥18.50 / $12.00。"""
        from .currency import format_money

        return format_money(self.amount, self.currency)

    @property
    def display_base_amount(self):
        """折算后的本位币金额（展示用）。"""
        from .currency import BASE_CURRENCY, format_money

        base = self.amount_base if self.amount_base is not None else self.amount
        return format_money(base, BASE_CURRENCY)

    def __str__(self):
        sign = "+" if self.type == "income" else "-"
        return f"{'收入' if self.type == 'income' else '支出'}：{self.display_title} {sign}¥{self.amount}"


# ── 凭证附件（P0-3）────────────────────────────────────────────────
# 报销、退货、争议账单都要留凭据。此前全库没有任何文件字段，
# OCR 有上传流程但识别完就丢，凭证无处可存。
RECEIPT_ALLOWED_EXT = ("jpg", "jpeg", "png", "webp", "gif", "heic", "pdf")
RECEIPT_IMAGE_EXT = ("jpg", "jpeg", "png", "webp", "gif", "heic")
RECEIPT_MAX_BYTES = 5 * 1024 * 1024


def receipt_upload_to(instance, filename):
    """上传路径：receipts/user_<id>/<年>/<月>/<uuid>.<ext>

    用 uuid 重命名而不是保留原始文件名，顺带解决三件事：
    路径穿越（../）、同名覆盖、以及中文文件名在部分存储上的编码问题。
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext = ext if ext in RECEIPT_ALLOWED_EXT else "bin"
    return f"receipts/user_{instance.user_id}/{timezone.now():%Y/%m}/{_uuid.uuid4().hex}.{ext}"


class Attachment(models.Model):
    """账目凭证：一张小票、一张截图、一份 PDF。

    安全约定：
    - 扩展名与 content_type 双重白名单（视图层做，模型只存元数据）
    - 落盘文件名是 uuid，原始文件名仅用于展示
    - 读取一律走受控视图（校验 user），不依赖 MEDIA_URL 静态服务：
      既能在 DEBUG=False 的生产环境工作，也不会暴露他人文件
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attachments"
    )
    expense = models.ForeignKey(
        Expense, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=receipt_upload_to)
    name = models.CharField(max_length=255, blank=True, help_text="原始文件名，仅用于展示")
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    is_image = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["expense", "is_deleted"]),
            models.Index(fields=["user", "is_deleted"]),
        ]

    def __str__(self):
        return self.name or self.file.name.rsplit("/", 1)[-1]

    @property
    def ext(self):
        return self.file.name.rsplit(".", 1)[-1].lower() if "." in self.file.name else ""

    @property
    def size_display(self):
        kb = self.size / 1024
        return f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "待办"
        IN_PROGRESS = "in_progress", "进行中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"
        ARCHIVED = "archived", "已归档"

    class Source(models.TextChoices):
        VOICE = "voice", "语音"
        TEXT = "text", "文本"
        MANUAL = "manual", "手动"
        AI = "ai", "AI"
        RULE = "rule", "规则"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default="todo")
    priority = models.PositiveSmallIntegerField(default=2, help_text="1 高，2 中，3 低")
    important = models.BooleanField(default=False, help_text="四象限：重要性（对标滴答清单 Eisenhower）")
    urgent = models.BooleanField(default=False, help_text="四象限：紧急性")
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default="manual")
    parent_task = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subtasks")
    recurrence_rule = models.CharField(max_length=20, default="none", help_text="none/daily/weekly/monthly/yearly")
    recurrence_day = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Day of month (1-31) for monthly/yearly")
    recurrence_days_before = models.PositiveSmallIntegerField(default=0, help_text="Remind N days before due date")
    raw_text = models.TextField(blank=True)
    tags = models.ManyToManyField("Tag", blank=True, related_name="tasks")
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-priority", "due_at"]
        indexes = [
            # 今日/本周/逾期任务：filter(user, is_deleted=False, status__in=[...], due_at__date...)
            models.Index(fields=["user", "is_deleted", "due_at"]),
            models.Index(fields=["user", "is_deleted", "status"]),
            # 四象限：按 importance×urgency 聚合活跃任务
            models.Index(fields=["user", "is_deleted", "important", "urgent"]),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    def next_occurrence(self):
        """Return the next due_at after completing this recurring task."""
        from calendar import monthrange
        from datetime import datetime, timedelta
        if not self.recurrence_rule or self.recurrence_rule == "none" or not self.due_at:
            return None
        base = self.due_at
        if isinstance(base, str):
            from django.utils import timezone
            base = datetime.fromisoformat(base.replace("Z", "+00:00"))
            if timezone.is_naive(base):
                base = timezone.make_aware(base)
        rule = self.recurrence_rule
        rday = self.recurrence_day or base.day
        if rule == "daily":
            return base + timedelta(days=1)
        if rule == "weekly":
            return base + timedelta(days=7)
        if rule == "monthly":
            y, m = base.year, base.month + 1
            if m > 12:
                y += 1
                m = 1
            last = monthrange(y, m)[1]
            return base.replace(year=y, month=m, day=min(rday, last))
        if rule == "yearly":
            return base.replace(year=base.year + 1)
        return None


class Tag(models.Model):
    """用户自定义标签，可挂到 Expense / Task / Note 上。

    与 Category 的本质区别（这也是为什么要两套机制）：
    - **Category 是单选树形分类**：一笔支出只能属于一个分类，用于统计口径
    - **Tag 是多维多值标签**：一笔支出可同时有「#旅行 #待报销 #和家人」，
      用于横向检索，不参与金额统计，避免重复计算

    因此看板/预算等金额聚合**只看分类不看标签**，防止同一笔钱被算两遍。
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=30)
    color = models.CharField(max_length=20, blank=True, default="", help_text="展示色（如 #f97316）；留空则跟随主题色")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_tag_per_user"),
        ]
        indexes = [
            models.Index(fields=["user", "name"]),
        ]

    def __str__(self):
        return f"#{self.name}"


class Account(models.Model):
    """账户 / 资金池：现金、银行卡、支付宝、微信、信用卡等。

    账户与分类是**正交**的两个维度：
    - 分类回答「钱花在哪」（餐饮、交通…）——用于支出结构分析
    - 账户回答「钱从哪出」（支付宝、招行卡…）——用于资金分布与余额

    一笔「用支付宝付的餐费」，分类是餐饮，账户是支付宝。
    """

    class Type(models.TextChoices):
        CASH = "cash", "现金"
        BANK = "bank", "银行卡"
        ALIPAY = "alipay", "支付宝"
        WECHAT = "wechat", "微信"
        CREDIT = "credit", "信用卡"
        OTHER = "other", "其他"

    # 各类型的默认图标，用户可覆盖
    DEFAULT_ICONS = {
        "cash": "💵", "bank": "🏦", "alipay": "🅰️",
        "wechat": "💬", "credit": "💳", "other": "💰",
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(max_length=50)
    type = models.CharField(max_length=20, choices=Type.choices, default="cash")
    currency = models.CharField(
        max_length=3, choices=CURRENCY_CHOICES, default="CNY",
        help_text="账户币种；本位币为 CNY",
    )
    icon = models.CharField(max_length=8, blank=True, help_text="留空则用类型默认图标")
    initial_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        help_text="启用时的初始余额；之后由流水推算",
    )
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "type", "name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_account_per_user"),
        ]
        indexes = [
            models.Index(fields=["user", "is_deleted", "is_active"]),
        ]

    def __str__(self):
        return f"{self.icon or self.DEFAULT_ICONS.get(self.type, '💰')} {self.name}"

    @property
    def display_icon(self):
        return self.icon or self.DEFAULT_ICONS.get(self.type, "💰")

    @property
    def balance(self):
        """当前余额 = 初始余额 + 收入 − 支出 − 转出 + 转入。

        只统计 status=confirmed 的流水：待确认的 AI 草稿不应影响真实余额。
        """
        from django.db.models import Sum

        zero = Decimal("0")
        base = self.initial_balance or zero

        def _sum(qs, **kw):
            return qs.filter(is_deleted=False, status="confirmed", **kw).aggregate(s=Sum("amount"))["s"] or zero

        income = _sum(self.transactions, type="income")
        expense = _sum(self.transactions, type="expense")
        # 转出：本账户减少
        out_transfer = _sum(self.transactions, type="transfer")
        # 转入：本账户增加
        in_transfer = _sum(self.incoming_transfers, type="transfer")

        return base + income - expense - out_transfer + in_transfer


class Note(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes")
    title = models.CharField(max_length=200)
    raw_text = models.TextField(blank=True)
    occurred_on = models.DateField(null=True, blank=True)
    tags = models.ManyToManyField("Tag", blank=True, related_name="notes")
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_deleted", "created_at"]),
            models.Index(fields=["user", "is_deleted", "occurred_on"]),
        ]

    def __str__(self):
        return f"随心记：{self.title}"


class Budget(models.Model):
    """Monthly budget — total or per-category. NULL category = total budget."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, null=True, blank=True, related_name="budgets")
    month = models.DateField(help_text="First day of month, e.g. 2026-08-01")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month", "category"]
        constraints = [
            models.UniqueConstraint(fields=["user", "category", "month"], name="unique_budget_per_user_category_month"),
        ]
        indexes = [
            # 预算/分析页按月拉取：filter(user, month...) 及按分类聚合
            models.Index(fields=["user", "month"]),
            models.Index(fields=["user", "category"]),
        ]

    def __str__(self):
        scope = self.category.name if self.category else "总预算"
        return f"{self.month:%Y-%m} {scope} ¥{self.amount}"


class SavingsGoal(models.Model):
    """储蓄目标：攒钱买某个东西 / 攒到某个数字（对标 MoneyWiz / 随手记的「心愿单 / 存钱罐」）。

    与 Budget 的区别：
    - Budget 回答「这个月某分类最多能花多少」（封顶、超支预警）
    - SavingsGoal 回答「我想攒到多少、已经攒了多少」（正向累积、达标庆祝）

    ``current_amount`` 用乐观锁无关的手工更新即可（单人量级），存入/取出走
    ``savings_goal_adjust``，保证不会扣成负数。
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="savings_goals")
    name = models.CharField(max_length=80)
    target_amount = models.DecimalField(max_digits=12, decimal_places=2)
    current_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    icon = models.CharField(max_length=8, blank=True, default="🎯", help_text="1-4 chars 可含 emoji")
    deadline = models.DateField(null=True, blank=True, help_text="目标达成期限（留空表示无期限）")
    note = models.TextField(blank=True, max_length=500)
    is_active = models.BooleanField(default=True, help_text="软删除开关")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.icon or '💰'} {self.name}（¥{self.current_amount}/¥{self.target_amount}）"

    @property
    def progress_pct(self):
        """已完成百分比（0–100，封顶 100）。目标为 0 时返回 0 避免除零。"""
        if self.target_amount <= 0:
            return 0
        return min(int(self.current_amount / self.target_amount * 100), 100)

    @property
    def remaining(self):
        """还差多少达标（封底 0）。"""
        return max(self.target_amount - self.current_amount, Decimal("0"))

    @property
    def is_reached(self):
        """是否已达标。"""
        return self.current_amount >= self.target_amount


class BalanceSnapshot(models.Model):
    """每日余额快照（净资产趋势图的数据底座）。

    净资产 = 所有活跃账户余额之和。账户余额由流水实时推算（见 Account.balance），
    但要画「随时间变化」的曲线，必须按天落库快照——否则每次都要重放全部历史流水。

    - 每日由 snapshot_balances 命令（或视图惰性）写入一条 (user, account, date)
    - 净值趋势 = 按 date 聚合 sum(balance)
    - 唯一约束 (user, account, date) 保证每日每账户仅一条，命令可安全重跑
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="balance_snapshots")
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="snapshots")
    date = models.DateField(help_text="快照日期")
    balance = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "account"]
        constraints = [
            models.UniqueConstraint(fields=["user", "account", "date"], name="unique_snapshot_per_user_account_date"),
        ]
        indexes = [
            models.Index(fields=["user", "date"]),
        ]

    def __str__(self):
        return f"{self.date} {self.account.name} ¥{self.balance}"


class RecurringExpense(models.Model):
    """Recurring bill: rent, phone, subscriptions, insurance, etc."""

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "每周"
        MONTHLY = "monthly", "每月"
        QUARTERLY = "quarterly", "每季度"
        YEARLY = "yearly", "每年"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recurring_expenses")
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="recurring_expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default="monthly")
    due_day = models.PositiveSmallIntegerField(help_text="Day of month (1-31)")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="留空表示无截止日期")
    remind_days_before = models.PositiveSmallIntegerField(default=3)
    is_active = models.BooleanField(default=True)
    # ── 自动入账（P0-1）────────────────────────────────────────────
    # auto_post 让用户保留控制权：只想被提醒、不想自动记账的可关掉。
    auto_post = models.BooleanField(
        default=True, help_text="到期自动生成一笔账目（关闭后仅提醒）",
    )
    # last_generated_date 记录「已经生成到哪一天」，是幂等的关键：
    # 每天最多推进一次，避免重复入账。
    last_generated_date = models.DateField(
        null=True, blank=True, help_text="已自动生成账目的截止日期（幂等游标）",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_active", "due_day"]
        indexes = [
            # scan_reminders / 固定支出列表：filter(user, is_active=True/False)
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        freq = dict(self.Frequency.choices).get(self.frequency, self.frequency)
        status = "" if self.is_active else " (已停用)"
        return f"{freq}{self.due_day}日 {self.name} ¥{self.amount}{status}"


class InstallmentPlan(models.Model):
    """Installment plan: track multi-period payments like loans, large purchases."""

    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        COMPLETED = "completed", "已还清"
        CANCELLED = "cancelled", "已取消"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="installment_plans")
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="installment_plans")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    installment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    total_periods = models.PositiveSmallIntegerField()
    paid_periods = models.PositiveSmallIntegerField(default=0)
    next_due_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-status", "next_due_date"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def remaining_amount(self):
        return self.total_amount - (self.installment_amount * self.paid_periods)

    def remaining_periods(self):
        return max(0, self.total_periods - self.paid_periods)

    def __str__(self):
        return f"{self.name} — {self.paid_periods}/{self.total_periods}期 ¥{self.installment_amount}/期"


class Reminder(models.Model):
    """Reminders: birthdays, bills, anniversaries, custom events."""

    class Type(models.TextChoices):
        BIRTHDAY = "birthday", "生日"
        BILL = "bill", "账单"
        CUSTOM = "custom", "自定义"
        TASK = "task", "任务"

    class Recurrence(models.TextChoices):
        NONE = "none", "不重复"
        DAILY = "daily", "每天"
        WEEKLY = "weekly", "每周"
        MONTHLY = "monthly", "每月"
        YEARLY = "yearly", "每年"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reminders")
    title = models.CharField(max_length=200)
    reminder_type = models.CharField(max_length=20, choices=Type.choices, default="custom")
    event_at = models.DateTimeField(help_text="事件发生的日期时间")
    remind_at = models.DateTimeField(help_text="提醒触发时间 = event_at - remind_days")
    remind_days_before = models.CharField(max_length=50, default="1", help_text="Comma-separated days, e.g. 1,7,15")
    recurrence_rule = models.CharField(max_length=20, choices=Recurrence.choices, default="none")
    is_enabled = models.BooleanField(default=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["remind_at"]
        indexes = [
            # 首页/扫描提醒高频：filter(user, is_enabled=True, remind_at__range)
            models.Index(fields=["user", "is_enabled", "remind_at"]),
            models.Index(fields=["user", "is_enabled", "event_at"]),
        ]

    def __str__(self):
        return f"🔔 {self.title} ({self.get_reminder_type_display()})"


class Countdown(models.Model):
    """倒计时 / 纪念日 — iOS Day Matters 风格模块。

    关键差异（vs Reminder）：
    - 用户视角：「生日还有 86 天」「考研还有 213 天」——主要是 **距离** 时间
    - 可选自动同步到 Reminder（提前 N 天在首页高亮）
    - 隐私：默认每个用户独立，没参与日历通用事件共享
    """

    class Direction(models.TextChoices):
        DOWN = "down", "倒计时（向目标日期倒数）"
        UP = "up", "纪念日（从过去日期数已经多少天）"

    class Recurrence(models.TextChoices):
        NONE = "none", "不重复"
        YEARLY = "yearly", "每年"
        MONTHLY = "monthly", "每月"
        WEEKLY = "weekly", "每周"
        DAILY = "daily", "每天"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="countdowns")
    title = models.CharField(max_length=80)
    target_date = models.DateField(help_text="目标日期")
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.DOWN)
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices, default=Recurrence.NONE)

    # 显示 / 个性化
    emoji = models.CharField(max_length=8, blank=True, default="", help_text="1-4 chars 可含 emoji")
    color = models.CharField(max_length=16, blank=True, default="", help_text="Hex color e.g. #5b8def")
    note = models.TextField(blank=True, max_length=500)
    show_on_home = models.BooleanField(default=True, help_text="是否在首页小板块显示")

    # 联动日历提醒（可选）
    sync_to_reminder = models.BooleanField(default=False, help_text="同步为日历提醒，提前 N 天提醒")
    reminder = models.OneToOneField(
        "Reminder", on_delete=models.SET_NULL, null=True, blank=True, related_name="countdown",
        help_text="已同步的日历提醒（删除 Countdown 时不会级联删除 Reminder）",
    )

    pinned = models.BooleanField(default=False, help_text="首页置顶")
    is_active = models.BooleanField(default=True, help_text="软删除开关")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pinned", "-created_at"]
        indexes = [
            models.Index(fields=["user", "is_active", "target_date"]),
        ]

    def __str__(self):
        arrow = "⏳" if self.direction == self.Direction.DOWN else "🎉"
        return f"{arrow} {self.title} → {self.target_date.isoformat()}"

    # ── 展示 helper ──────────────────────────────────────────────────
    def next_occurrence(self, today=None):
        """Return the next / current occurrence date based on recurrence.

        For DOWN direction + YEARLY recurrence, if today is past target_date,
        roll forward to next year (so the countdown never shows negative days).
        """
        from datetime import timedelta
        today = today or timezone_now_localdate()
        d = self.target_date
        if self.recurrence == self.Recurrence.NONE:
            return d
        if self.recurrence == self.Recurrence.YEARLY:
            # jump forward year-by-year until >= today
            try:
                while d < today:
                    d = d.replace(year=d.year + 1)
            except ValueError:  # 2/29 in non-leap year
                d = d.replace(year=d.year + 1, day=28)
            return d
        if self.recurrence == self.Recurrence.MONTHLY:
            while d < today:
                y, m = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
                try:
                    d = d.replace(year=y, month=m)
                except ValueError:
                    d = d.replace(year=y, month=m, day=28)
            return d
        if self.recurrence == self.Recurrence.WEEKLY:
            while d < today:
                d = d + timedelta(days=7)
            return d
        if self.recurrence == self.Recurrence.DAILY:
            # for daily, target_date is just the start day; next = today
            return today
        return d

    def days_diff(self, today=None):
        """Return signed day count (negative = past)."""
        today = today or timezone_now_localdate()
        target = self.next_occurrence(today) if self.direction == self.Direction.DOWN else self.target_date
        return (target - today).days

    @property
    def accent_color(self):
        return self.color or "#5b8def"  # default brand blue


def timezone_now_localdate():
    """Small helper — avoid importing timezone at module load time."""
    from django.utils import timezone
    return timezone.localdate()


# ── AI Conversation models ──────────────────────────────────────────


class ConversationLog(models.Model):
    """Raw AI input — never stores API keys."""

    class InputType(models.TextChoices):
        VOICE = "voice", "语音"
        TEXT = "text", "文本"

    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        CONFIRMED = "confirmed", "已确认"
        CANCELLED = "cancelled", "已取消"
        ERROR = "error", "解析失败"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    raw_text = models.TextField()
    input_type = models.CharField(max_length=20, choices=InputType.choices, default="text")
    model = models.CharField(max_length=100, blank=True, help_text="AI model name, e.g. deepseek-v3")
    token_count = models.PositiveIntegerField(null=True, blank=True, help_text="Total tokens used")
    cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True, help_text="Estimated cost in USD")
    status = models.CharField(max_length=20, choices=Status.choices, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        snippet = self.raw_text[:60] + "…" if len(self.raw_text) > 60 else self.raw_text
        return f"[{self.get_status_display()}] {snippet}"


class ParseResult(models.Model):
    """AI parse output — draft only, never auto-saved to business tables."""

    conversation = models.ForeignKey(ConversationLog, on_delete=models.CASCADE, related_name="parse_results")
    confidence = models.FloatField(default=0.0, help_text="0.0–1.0")
    draft_json = models.JSONField(default=dict, help_text="Raw AI output as JSON")
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ParseResult(confidence={self.confidence:.2f}, confirmed={self.is_confirmed})"


class ProposedAction(models.Model):
    """One pending action from AI parse — user confirms before saving to real models."""

    class ActionType(models.TextChoices):
        CREATE_EXPENSE = "create_expense", "新建支出"
        CREATE_TASK = "create_task", "新建任务"
        CREATE_REMINDER = "create_reminder", "新建提醒"
        CREATE_NOTE = "create_note", "新建记事"
        CREATE_RECURRING_EXPENSE = "create_recurring_expense", "新建固定账单"
        CREATE_DAILY_REMINDER = "create_daily_reminder", "新建每日提醒"

    parse_result = models.ForeignKey(ParseResult, on_delete=models.CASCADE, related_name="proposed_actions")
    action_type = models.CharField(max_length=30, choices=ActionType.choices)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=50, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    event_at = models.DateTimeField(null=True, blank=True)
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.get_action_type_display()}：{self.title}"


class Review(models.Model):
    """Weekly or monthly review — draft generated, user confirms."""

    class Period(models.TextChoices):
        WEEKLY = "weekly", "每周"
        MONTHLY = "monthly", "每月"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    period = models.CharField(max_length=20, choices=Period.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    content = models.TextField(help_text="Markdown content of the review")
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["user", "period", "period_start"], name="unique_review_per_user_period"),
        ]
        indexes = [
            models.Index(fields=["user", "period_start"]),
        ]

    def __str__(self):
        return f"{self.get_period_display()}复盘 {self.period_start}"


class Suggestion(models.Model):
    """Data-backed suggestion — every suggestion must cite evidence."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="suggestions")
    title = models.CharField(max_length=300)
    evidence = models.TextField(help_text="Data basis for this suggestion, e.g. '餐饮本月 ¥820，比过去3月均值 ¥670 高 22%'")
    category = models.CharField(max_length=40, blank=True, help_text="e.g. spending/task/reminder/budget")
    feedback = models.CharField(max_length=20, blank=True, help_text="useful/not_useful/dismissed")
    generated_at = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"💡 {self.title}"


def _default_parse_job_uuid():
    """ParseJob.uuid 的默认值工厂（必须是可调用对象）。

    写成 default=uuid.uuid4().hex 会在类定义时求值一次并固化为常量，
    与 unique=True 冲突（同进程内第二条记录即违反唯一约束），
    且每次 makemigrations 都会因默认值变化而生成多余的迁移。
    """
    return _uuid.uuid4().hex


class ParseJob(models.Model):
    """AI 解析异步任务表。

    解析文本时，规则解析同步返回；需要 AI 时改为后台线程执行并写入本表，
    前端通过 ``/api/parse-status/<uuid>/`` 轮询结果，避免 AI 调用（最长 ~30s）
    阻塞 Web worker。仅单用户量级的本地辅助表，不做复杂约束。
    """

    STATUS = [
        ("pending", "等待中"),
        ("running", "解析中"),
        ("done", "已完成"),
        ("error", "失败"),
    ]

    uuid = models.CharField(max_length=32, unique=True, db_index=True, default=_default_parse_job_uuid,
                            help_text="对外暴露的任务标识，用于轮询，无业务含义")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="parse_jobs")
    raw_text = models.TextField(help_text="待解析的原始文本")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    result = models.JSONField(null=True, blank=True, help_text="解析结果（与 route_parse 返回结构一致）")
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "-created_at"], name="parsejob_user_status_idx")]

    def __str__(self):
        return f"ParseJob[{self.uuid}] {self.status}"


class Badge(models.Model):
    """用户达成成就的持久化记录（游戏化激励，P2）。

    徽章的展示名/图标/规则定义在 life/gamification.py 的 BADGE_DEFS，
    这里只存「谁、哪枚、何时点亮」，避免规则与数据耦合。
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badges")
    key = models.CharField(max_length=40, help_text="对应 BADGE_DEFS 中的 key")
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-earned_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="unique_badge_per_user"),
        ]
        indexes = [models.Index(fields=["user", "key"])]

    def __str__(self):
        return f"Badge[{self.key}] user={self.user_id}"
