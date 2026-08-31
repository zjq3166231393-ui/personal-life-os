"""开发库迁移漂移防护。

问题
----
开发库（``db.sqlite3``）落后于代码时，任何读取新字段的页面都会抛
``OperationalError: no such column: ...``。登录后默认跳转首页，首页一崩就
表现为「登不进去、全是报错代码」，而真正的原因只是少跑了一次 ``migrate``。

更麻烦的是：**测试跑在独立的测试库上，所以测试全绿完全发现不了开发库漂移。**

Django 自身会输出 ``You have N unapplied migration(s)`` 提示，但它在启动日志里
一闪而过，极易被忽略。本模块把它升级为「醒目提示 + 自动修复」，由 ``manage.py``
在 ``runserver`` 启动前调用。

只在 ``settings.DEBUG`` 为真时介入；生产由 gunicorn 启动，不会走到这里。
"""

from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader


def pending_migrations():
    """返回 ``[(app_label, name), ...]``：磁盘上有、但数据库尚未应用的迁移。

    数据库与代码完全同步时返回空列表。
    """
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    applied = set(loader.applied_migrations)
    return sorted(key for key in loader.disk_migrations if key not in applied)


def format_pending(pending):
    """把待迁移列表格式化成 ``accounts.0009_x, life.0038_y`` 形式。"""
    return ", ".join(f"{app}.{name}" for app, name in pending)


def auto_migrate_if_needed(write=print, auto=True):
    """检测未应用迁移；``auto`` 为真时自动执行 ``migrate``。

    返回本次实际修复的迁移数量（0 表示原本就同步，或只提示未修复）。
    ``write`` 用于输出，默认 ``print``，便于测试替换。
    """
    pending = pending_migrations()
    if not pending:
        return 0

    write("")
    write("=" * 72)
    write(f"⚠️  开发库落后于代码：检测到 {len(pending)} 个未应用的迁移")
    write(f"    {format_pending(pending)}")
    write("=" * 72)

    if not auto:
        write("已指定 --no-auto-migrate：跳过自动修复。")
        write("请手动执行： python manage.py migrate")
        write("")
        return 0

    write("正在自动应用迁移…")
    try:
        call_command("migrate", interactive=False, verbosity=1)
    except Exception as exc:  # noqa: BLE001 - 不让迁移失败掩盖原始原因
        write(f"❌ 自动迁移失败：{exc}")
        write("   请手动执行 `python manage.py migrate` 查看完整错误。")
        write("")
        return 0

    left = pending_migrations()
    if left:
        write(f"⚠️  迁移执行后仍有 {len(left)} 个未应用，请手动排查：")
        write(f"    {format_pending(left)}")
        write("")
        return 0

    write(f"✅ 迁移已补齐（{len(pending)} 个），开发库与代码同步。")
    write("")
    return len(pending)
