"""Backfill / refresh daily balance snapshots for the net-worth trend chart.

Each active account gets one ``BalanceSnapshot`` per day holding its balance at
day-end. Re-running is safe: rows use ``bulk_create(ignore_conflicts=True)`` so
existing snapshots (per user+account+date unique constraint) are left untouched.

Usage:
    python manage.py snapshot_balances                 # all users, last 365 days
    python manage.py snapshot_balances --days=180      # narrower window
    python manage.py snapshot_balances --username=demo # single user
    python manage.py snapshot_balances --all           # full history (no 365d cap)
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Min
from django.utils import timezone

from life.models import Account, BalanceSnapshot
from life.services import daily_balance_series


class Command(BaseCommand):
    help = "Backfill daily balance snapshots for the net-worth trend chart."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=365, help="Lookback window in days (default 365).")
        parser.add_argument("--username", default=None, help="Limit to a single user.")
        parser.add_argument("--all", action="store_true", help="Use full history instead of capping at --days.")

    def handle(self, days, username, all, **options):
        users = User.objects.all()
        if username:
            users = users.filter(username=username)

        today = timezone.localdate()
        cap = None if all else today - timedelta(days=days)

        total = 0
        for user in users:
            for account in Account.objects.filter(user=user, is_deleted=False, is_active=True):
                # 起点 = 最早交易日起；超过 cap（默认 365 天）则截断到 cap，避免回填过远的空窗
                et = account.transactions.filter(is_deleted=False).aggregate(m=Min("occurred_at"))["m"]
                it = account.incoming_transfers.filter(is_deleted=False).aggregate(m=Min("occurred_at"))["m"]
                candidates = [x for x in (et, it) if x]
                earliest = min(c.date() for c in candidates) if candidates else today
                start = earliest if (cap is None or earliest > cap) else cap

                series = daily_balance_series(account, start, today)
                snaps = [
                    BalanceSnapshot(user=user, account=account, date=d, balance=b)
                    for d, b in series
                ]
                BalanceSnapshot.objects.bulk_create(snaps, ignore_conflicts=True)
                total += len(snaps)
                self.stdout.write(f"  {account} → {len(snaps)} 天快照 ({start} ~ {today})")

        self.stdout.write(self.style.SUCCESS(f"快照回填完成：共 {total} 条 BalanceSnapshot。可重复运行，已存在日期将被忽略。"))
