#!/usr/bin/env python
import os
import sys

# 仅在 runserver 时生效的开关：检测到未应用迁移只提示、不自动修复。
_NO_AUTO_FLAG = "--no-auto-migrate"


def _guard_migrations(argv):
    """开发模式下，启动 ``runserver`` 前自动补齐未应用的数据库迁移。

    背景：开发库落后于代码时页面会抛 ``no such column``，登录后首页即崩，
    表现为「登不进去」。而测试跑在独立测试库上，**测试全绿发现不了这个问题**。
    实现与说明见 ``life/migration_guard.py``。

    任何异常都不允许阻断服务器启动，因此整体包在 try/except 里。
    """
    if len(argv) < 2 or argv[1] != "runserver":
        return

    auto = True
    if _NO_AUTO_FLAG in argv:
        auto = False
        argv.remove(_NO_AUTO_FLAG)  # 后续 runserver 不认识这个参数

    # 尊重命令行上的 --settings=xxx / --settings xxx，避免守卫连错库。
    for i, arg in enumerate(argv):
        if arg.startswith("--settings="):
            os.environ["DJANGO_SETTINGS_MODULE"] = arg.split("=", 1)[1]
            break
        if arg == "--settings" and i + 1 < len(argv):
            os.environ["DJANGO_SETTINGS_MODULE"] = argv[i + 1]
            break

    try:
        import django
        from django.conf import settings

        django.setup()
        if not settings.DEBUG:
            return  # 生产环境不介入

        from life.migration_guard import auto_migrate_if_needed

        auto_migrate_if_needed(auto=auto)
    except Exception as exc:  # noqa: BLE001 - 防护逻辑绝不阻断服务器启动
        print(f"[migration-guard] 已跳过自动迁移检查：{exc}")


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    _guard_migrations(sys.argv)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
