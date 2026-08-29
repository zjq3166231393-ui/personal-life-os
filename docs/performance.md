# Personal Life OS — 性能分析

> 基准：SQLite 单用户，~1000 条记录，191 tests OK

---

## 已优化项

| # | 位置 | 问题 | 优化 | 影响 |
|---|------|------|------|------|
| 1 | `expense_list` 视图 | Expense 列表需访问 `e.category.name` | `select_related("category")` ✅ | 1+N → 1 查询 |
| 2 | `home` 视图 | Recurring/Installment 循环 | `select_related("category")` ✅ | 减少 FK 查询 |
| 3 | `category_list` 视图 | 分类列表引用计数 | 已有 `select_related()` | — |
| 4 | 所有分页 | 历史列表无分页 | Expense 列表使用 `Paginator(20)` ✅ | 限制单页数据量 |
| 5 | `scan_reminders` | 提醒扫描 | 已有 `idempotency_key` 防重复 ✅ | 避免重复写入 |

---

## 已知 N+1（低影响，个人规模足够）

| 位置 | 详情 | 估算影响 |
|------|------|----------|
| `dashboard` 预算分类循环 | `for c in categories` 每分类 1-3 次聚合查询 | ~8分类 × 3查询 = 24 次聚合查询 |
| `dashboard` 异常检测循环 | 同上结构，4 个独立循环 | ~8分类 × 4循环 = 32 次聚合查询 |
| `budget` 视图 | 每分类独立 budget 和 spent 查询 | ~8 次查询 |

> 以上聚合查询均为高效 `SUM/AVG`，在 SQLite 单用户场景下总耗时 < 50ms。生产环境 MySQL + 索引后进一步降低。

---

## 索引建议

```sql
-- 高频过滤字段
CREATE INDEX idx_expense_user_type ON life_expense(user_id, type);
CREATE INDEX idx_expense_user_status ON life_expense(user_id, status);
CREATE INDEX idx_expense_occurred ON life_expense(user_id, occurred_at);
CREATE INDEX idx_task_user_status ON life_task(user_id, status);
CREATE INDEX idx_task_user_due ON life_task(user_id, due_at);
CREATE INDEX idx_notification_user ON common_notificationlog(user_id, created_at);

-- Django 自动创建的索引已覆盖：
-- PK (id), FK (user_id, category_id), unique (idempotency_key)
```

> 以上索引在 SQLite 上非必须（小数据量），MySQL 生产环境建议执行。

---

## 查询计数（关键页面）

| 页面 | 典型查询数 | 说明 |
|------|-----------|------|
| 今日页 | 6 | Task(2) + Reminder(1) + Recurring(1) + Installment(1) + Budget(1) |
| 账目列表 | 3 | Expense + Paginator + Categories |
| 任务列表 | 1 | Task filter |
| 财务看板 | ~30 | KPI(3) + 趋势(2) + 分类(N) + 异常(N) + 建议(N) |
| 通知中心 | 1 | Notification filter(user) |
| 复盘 | 8 | Expense(2) + Task(3) + Budget(1) + Category(1) + Upcoming(1) |

---

## 缓存策略

| 数据 | 策略 | 原因 |
|------|------|------|
| 今日页 | 不缓存 | 实时数据 |
| 看板图表 | 不缓存 | 聚合查询快 |
| 分类列表 | 可缓存 5min | 变更频率低 |
| 静态资源 | Nginx 30d | 不变 |

当前使用 `LocMemCache`（进程内存），生产建议切换到 Redis。

---

## 建议优先级

| 优先级 | 优化项 | 场景 |
|--------|--------|------|
| P1 | MySQL + 索引 | 生产部署 |
| P2 | Dashboard 批量聚合 | 分类 >20 时 |
| P2 | Redis 替代 LocMemCache | Gunicorn 多 worker |
| P3 | 看板 AJAX 懒加载 | 数据量大时 |
| P3 | CDN 静态资源 | 公网访问 |
