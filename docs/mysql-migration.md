# Personal Life OS — MySQL 迁移指南

## 流程总览

```
开发环境 (SQLite) ──→ 生产环境 (MySQL)
    │                      │
    ├ 数据量小              ├ 数据量大
    ├ 零配置                ├ 需 .env 配置
    └ python manage.py      └ mysql + gunicorn + nginx
```

---

## 1. 创建最小权限数据库账号

```sql
-- 以 root 登录 MySQL
CREATE DATABASE personal_life_os CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'lifeos'@'127.0.0.1' IDENTIFIED BY 'your-strong-password-here';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER
  ON personal_life_os.* TO 'lifeos'@'127.0.0.1';

FLUSH PRIVILEGES;
```

> 不给 DROP、TRUNCATE、GRANT 权限。迁移时临时授 CREATE/ALTER/INDEX，迁移后可收回。

---

## 2. 配置 .env

```bash
DJANGO_ENVIRONMENT=production
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=your-domain.com
DJANGO_SECRET_KEY=<生成的随机50位密钥>

MYSQL_DATABASE=personal_life_os
MYSQL_USER=lifeos
MYSQL_PASSWORD=your-strong-password-here
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
```

---

## 3. 迁移前备份

```powershell
# SQLite 备份
Copy-Item db.sqlite3 "db-backup-$(Get-Date -Format 'yyyyMMdd-HHmm').sqlite3"

# 导出为 SQL（可选，用于手动恢复）
python manage.py dumpdata --indent 2 > "fixture-$(Get-Date -Format 'yyyyMMdd').json"
```

---

## 4. 执行迁移

```powershell
# 1. 验证配置
python manage.py check --deploy

# 2. 运行迁移（MySQL 数据库必须已创建）
python manage.py migrate

# 3. 创建超级用户
python manage.py createsuperuser

# 4. 收集静态文件
python manage.py collectstatic --noinput

# 5. 启动
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
```

---

## 5. 迁移后验证

```powershell
# 自动验证金额、时间、索引
python manage.py verify_db

# Django 系统检查
python manage.py check

# 跑测试
python manage.py test
```

### 手动验证 SQL

```sql
-- 验证金额字段类型
SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'personal_life_os'
  AND DATA_TYPE = 'decimal';

-- 预期：amount 字段为 decimal(12,2)，monthly_budget 为 decimal(12,2)

-- 验证索引
SHOW INDEX FROM life_expense;
SHOW INDEX FROM common_notificationlog;

-- 验证字符集
SELECT TABLE_NAME, TABLE_COLLATION
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'personal_life_os';
-- 预期全部为 utf8mb4_unicode_ci
```

---

## 6. 验证命令

```powershell
python manage.py verify_db
# 检查：
#   ✅ 金额字段类型 (Decimal, not float)
#   ✅ 时间字段 (DateTimeField with timezone)
#   ✅ 索引 (user_id, status, idempotency_key)
#   ✅ 软删除逻辑 (is_deleted + deleted_at)
#   ✅ 外键完整性
#   ✅ 字符集 (utf8mb4)
```

---

## 7. 回滚方案

```powershell
# 方案 A：使用备份 SQLite 文件
Copy-Item "db-backup-YYYYMMDD-HHmm.sqlite3" db.sqlite3 -Force
# 改 .env 清空 MYSQL_* 参数 → 自动回退 SQLite

# 方案 B：从 fixture 恢复
python manage.py flush --noinput
python manage.py loaddata fixture-YYYYMMDD.json
```

---

## 8. 安全提醒

- [ ] `.env` 未提交到 Git
- [ ] MySQL 密码强度 ≥ 16 位随机
- [ ] 数据库仅监听 127.0.0.1
- [ ] 生产环境 `DEBUG=False`
- [ ] `SECRET_KEY` 为随机 50 位字符串
- [ ] MySQL 账号仅 `lifeos` 最小权限
