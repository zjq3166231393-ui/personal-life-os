"""把到期的固定支出推进为真实账目（P0-1）。

Usage:
    python manage.py generate_recurring              # 全部用户
    python manage.py generate_recurring --dry-run    # 预览，不写库
    python manage.py generate_recurring --user=miles # 只处理指定用户名

幂等：同一到期日只会入账一次（靠 RecurringExpense.last_generated_date 游标），
可以安全地放进 cron 每天执行。
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from life.recurring import generate_due_recurring


class Command(BaseCommand):
    help = "Generate real Expense records for due recurring bills (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
        parser.add_argument("--user", help="Only process this username")

    def handle(self, dry_run=False, user=None, **options):
        target = None
        if user:
            User = get_user_model()
            target = User.objects.filter(username=user).first()
            if target is None:
                self.stderr.write(self.style.ERROR(f"用户不存在：{user}"))
                return

        stats = generate_due_recurring(user=target, dry_run=dry_run)

        prefix = "[预览] " if dry_run else ""
        self.stdout.write(
            f"{prefix}扫描固定支出 {stats['plans']} 条 · "
            f"生成 {stats['created']} 笔 · 已存在跳过 {stats['skipped']} 笔"
        )
        for name, due in stats["dates"]:
            self.stdout.write(f"  - {name} @ {due}")

        if stats["created"]:
            self.stdout.write(self.style.SUCCESS("完成"))
        else:
            self.stdout.write("没有需要入账的到期账单")
