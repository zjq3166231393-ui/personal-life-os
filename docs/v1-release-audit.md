# Personal Life OS — V1 发布前工程审计报告

> 审计日期：2026-08-11 | 分支：`release/v0.9.0` | 测试：190 OK | Django 5.2.17

---

## 一、项目结构

```
personal-life-os/     (70 Python files, 40 templates, 20 migrations)
├── config/           Django 配置
├── life/             核心业务（模型/视图/解析/命令）
├── accounts/         用户与权限
├── common/           审计/通知/推送/邮件
├── finance/          V0.3 预留骨架
├── planning/         V0.4 预留骨架
├── notes/            V0.4 预留骨架
├── capture/          V0.5 预留骨架
├── deploy/           生产部署配置
├── docs/             9 份文档
├── tests/            评测数据集
└── static/           静态文件 + 离线页面
```

| 项目 | 状态 |
|------|------|
| Python 文件数 | 70（不含 venv/migrations） |
| 模板数 | 40（3 个 app） |
| 迁移数 | 20（life:15, common:2, accounts:1, 其他标准 Django） |
| `__pycache__` 目录 | 14 个（.gitignore 已覆盖） |
| `.env` 文件 | 不存在（正确） |
| `db.sqlite3` | 344 KB（.gitignore 已覆盖） |

---

## 二、Django 配置

| 检查项 | 文件 | 状态 | 备注 |
|--------|------|------|------|
| SECRET_KEY | `config/settings.py:9` | ⚠ P1 | 默认值过短且可预测 |
| DEBUG | `config/settings.py:10` | ✅ | 环境变量控制 |
| ALLOWED_HOSTS | `config/settings.py:11` | ✅ | 环境变量控制 |
| ENVIRONMENT 分离 | `config/settings.py:87` | ✅ | production 和 development 分支 |
| HTTPS 安全配置 | `config/settings.py:88-108` | ✅ | production 模式下自动启用 |
| 密码校验器 | `config/settings.py:110-115` | ✅ | production 模式下启用 |
| 中间件 | `config/settings.py:29-37` | ✅ | 标准 + LoginRateLimit |
| 日志 | `config/settings.py:121-156` | ✅ | 轮转文件 + 控制台 |
| MySQL 切换 | `config/settings.py:53-56` | ✅ | 环境变量控制 |
| Cache | `config/settings.py:78-82` | ⚠ P2 | LocMemCache 多进程不共享 |
| `--deploy` 检查 | — | ⚠ 7 warnings | development 模式预期行为 |

### P1: SECRET_KEY 默认值不安全

- **文件**: `config/settings.py:9`
- **风险**: 生产环境忘记设 `.env` 时使用可预测密钥
- **建议**: 将默认值改为空或 `__import__("secrets").token_urlsafe(50)`，为空时拒绝启动

### P2: LocMemCache 多进程不共享

- **文件**: `config/settings.py:78-82`
- **影响**: Gunicorn 多 worker 时，登录限流计数器不准确
- **建议**: 生产环境改用 Redis 或数据库缓存

---

## 三、模型与迁移

| 检查项 | 状态 | 详情 |
|--------|------|------|
| 迁移一致性 | ✅ | `makemigrations --check` → No changes |
| 迁移数量 | ✅ | 20 个，已全部应用 |
| 模型分布 | ⚠ P2 | 所有核心模型集中于 `life/models.py` |
| 字段类型 | ✅ | 金额全部 DecimalField |
| 软删除覆盖 | ✅ | Expense/Task/Note 均有 is_deleted + deleted_at |
| audit 字段 | ✅ | created_at 普遍存在，updated_at 部分存在 |
| 遗留 Entry 模型 | ⚠ P2 | `life.Entry` 仍存在，与 Expense/Task/Note 并存 |

### P2: 模型集中在 life/

- **文件**: `life/models.py`（16 个模型）
- **影响**: 单文件过大，后续维护困难
- **建议**: 按领域拆分到对应 app（Expense→finance, Task→planning 等），V1 之后做

### P2: Entry 模型遗留

- **文件**: `life/models.py:5-41`
- **影响**: Entry 与新模型并存，数据冗余
- **建议**: 标记 deprecated，V1 后移除或合并

---

## 四、用户隔离

| 检查项 | 状态 |
|--------|------|
| 所有查询过滤 `request.user` | ✅ 79 处出现 |
| 权限守卫 `_check_owner()` | ✅ 所有 CRUD 视图 |
| `objects.all()` 滥用 | ✅ 仅 1 处（`parent.subtasks.all()`，受父对象约束） |
| 数据隔离测试 | ✅ `DataIsolationTests` 5 个 |
| API 未登录保护 | ✅ `@login_required` → 302 |

---

## 五、认证与授权

| 检查项 | 状态 | 备注 |
|--------|------|------|
| 登录 | ✅ | Django auth + Bootstrap 页面 |
| 注册 | ✅ | 自动登录 + Profile 创建 |
| 登出 | ✅ | GET/POST 双支持 |
| 密码重置 | ✅ | 4 页完整流程 |
| 登录限流 | ✅ | 5次/15分钟 IP 锁定 |
| Session 超时 | ✅ | 2h 空闲 + 关闭浏览器 |
| `csrf_exempt` | ✅ | 无使用 |
| Admin 保护 | ⚠ P2 | 无额外 IP 限制 |

### P2: Admin 无 IP 限制

- **文件**: `config/settings.py`（Admin 默认启用）
- **建议**: 生产环境在 Nginx 层限制 `/admin/` 访问 IP

---

## 六、AI 调用边界

| 检查项 | 状态 |
|--------|------|
| 规则优先 | ✅ `route_parse()` confidence=high + single-intent → 跳过 AI |
| AI 兜底 | ✅ FakeProvider 无 Key 时回退 |
| Schema 校验 | ✅ `validate_ai_response()` 7 种 intent |
| API Key 存储 | ✅ `.env` 仅，代码/日志不存 |
| 敏感内容检测 | ✅ `_check_sensitive()` 4 种模式 |
| 每日上限 | ✅ `daily_ai_limit` |
| 用户开关 | ✅ `ai_parsing_enabled` |
| AI 不直接写库 | ✅ `ProposedAction` 草稿 → 确认卡 → 事务保存 |
| 成本记录 | ✅ `ConversationLog.token_count` / `cost` 字段 |

---

## 七、通知与定时任务

| 检查项 | 状态 | 备注 |
|--------|------|------|
| 站内通知 | ✅ | `NotificationLog` + `notification_list` |
| 重复防护 | ✅ | `idempotency_key` |
| Push 订阅 | ✅ | `PushSubscription` + SW push 事件 |
| 邮件兜底 | ✅ | `email_util.py`，不含金额细节 |
| 定时扫描 | ✅ | `scan_reminders` (Reminder/Task/Recurring) |
| 干运行 | ✅ | `--dry-run` |
| cron 配置 | ✅ | `docs/deployment.md` 中 |

---

## 八、测试覆盖

| 模块 | 测试数 | 类型 |
|------|--------|------|
| accounts | 24 | auth/profile/isolate |
| parser | 14 | 6 例句 + 8 边界 |
| expense/model | 9 | CRUD/Decimal |
| task/model | 5 | status/parent/desc |
| task/views | 9 | filter/action |
| recurrence | 8 | monthly/eom/renew/dup |
| category | 14 | CRUD/deactivate/refs |
| budget | 11 | total/cat/overspent |
| recurring/exp | 8 | CRUD/remind |
| installment | 9 | CRUD/pay/overpay |
| reminder | 6 | CRUD/toggle |
| AI schema | 18 | 6 valid + 12 reject |
| AI provider | 7 | fake/real |
| AI router | 7 | rule/ai/fallback |
| AI model | 7 | conv/parse/action |
| multi-intent | 3 | 2_exp+1_task |
| confirm | 3 | batch/rollback |
| audit | 7 | log/no-pwd/fields |
| scan | 7 | dry-run/dup/task |
| data_check | 4 | amount/cat/deleted |
| parser eval | 3 | fixture/crash/all |
| smoke | 5 | 5 skeleton apps |
| **合计** | **190** | |

### 缺失覆盖

| 领域 | 风险 |
|------|------|
| 通知推送失败处理 | P2 |
| 邮件发送重试边界 | P2 |
| Web Push 端到端 | P2 (需浏览器环境) |
| 恢复命令端到端 | P2 |

---

## 九、依赖

| 包 | 版本 | 用途 | 风险 |
|----|------|------|------|
| Django | ≥5.0,<6.0 | 框架 | ✅ 活跃维护 |
| PyMySQL | ≥1.1 | MySQL 驱动 | ✅ |
| python-dotenv | ≥1.0 | 环境变量 | ✅ |
| Chart.js | 4.4 CDN | 图表 | ✅ CDN，无本地依赖 |
| Bootstrap | 5.3 CDN | UI | ✅ CDN，无本地依赖 |

**结论**: 依赖极少，无已知漏洞风险。

---

## 十、文档

| 文件 | 状态 | 内容 |
|------|------|------|
| README.md | ⚠ P2 | V0.1 内容，未更新到 V0.8 |
| docs/threat-model.md | ✅ | 9 场景 |
| docs/deployment.md | ✅ | 完整部署流程 |
| docs/mysql-migration.md | ✅ | 迁移 + 回滚 |
| docs/metric-definitions.md | ✅ | 10 指标 |
| docs/architecture.md | ⚠ P2 | V0.2 内容 |
| docs/data-model.md | ⚠ P2 | V0.2 内容 |
| docs/testing.md | ✅ | 测试约定 |
| docs/v0.2-*.md | ⚠ P2 | 历史文档，可归档 |
| deploy/ | ✅ | 3 个配置文件 |

### P2: README 和架构文档过时

- **文件**: `README.md`, `docs/architecture.md`, `docs/data-model.md`
- **影响**: 新贡献者无法了解当前项目状态
- **建议**: V1 前更新 README 和架构文档

---

## 十一、部署配置

| 检查项 | 状态 |
|--------|------|
| Gunicorn 配置 | ✅ `deploy/gunicorn.conf.py` |
| Nginx 配置 | ✅ `deploy/nginx.conf` |
| systemd 服务 | ✅ `deploy/lifeos.service` |
| 健康检查 | ✅ `/health/` 端点 |
| 静态文件 | ✅ `STATIC_ROOT` + collectstatic |
| 定时任务 | ✅ cron 配置在 deployment.md |
| `.env.example` | ✅ 覆盖所有必要配置 |

---

## 十二、性能风险

| 风险 | 级别 | 说明 |
|------|------|------|
| 无数据库索引 | P2 | 部分 user_id/status 列缺显式索引 |
| SQLite 锁竞争 | P2 | 默认数据库，生产应切换 MySQL |
| LocMemCache | P2 | 多进程不共享 |
| 未使用 CDN 静态文件 | P2 | Django 直接服务，生产应 Nginx |
| Dashboard 多查询 | P2 | 看板页面执行 20+ 次查询 |
| N+1 查询 | P2 | `category_list` 已用 `select_related`，其他需检查 |

---

## 十三、安全风险汇总

| 级别 | 数量 | 关键项 |
|------|------|--------|
| **P0** | 0 | 无阻塞性问题 |
| **P1** | 1 | SECRET_KEY 默认值不安全 |
| **P2** | 10 | 模型集中、Entry 遗留、Admin IP、Cache、README 过时、索引缺失等 |

---

## 十四、V1 发布前建议

| 优先级 | 建议 | 任务 |
|--------|------|------|
| P1 | 修复 SECRET_KEY 默认值 | V0.9-03 |
| P2 | 更新 README.md | V0.9-04 |
| P2 | 添加缺失的数据库索引 | V0.9-05 |
| P2 | 标记 Entry 模型 deprecated | V0.9-05 |
| P2 | Admin 访问限制 | V0.9-03 |
| P2 | 更新架构文档 | V0.9-04 |
| P2 | Dashboard N+1 优化 | V0.9-05 |
