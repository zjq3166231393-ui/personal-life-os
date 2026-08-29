# Personal Life OS — 决策日志

记录项目过程中的关键技术决策和取舍。

---

## 1. Django 单体架构

**日期**: V0.1  
**决策**: 使用 Django 单体而非微服务  
**理由**: 个人使用，单用户低并发。单体架构开发效率高、运维简单  
**替代方案**: FastAPI + React（拒绝：增加复杂度）

---

## 2. SQLite 开发 / MySQL 生产

**日期**: V0.1  
**决策**: 开发环境默认 SQLite，生产通过 `.env` 切换 MySQL  
**理由**: SQLite 零配置适合快速开发，MySQL 适合长期稳定存储

---

## 3. Bootstrap + Django Templates

**日期**: V0.1  
**决策**: 不使用 React/Vue 等前端框架  
**理由**: 减少技术栈复杂度，Django Templates 对个人项目足够  
**底线**: 永不引入前端构建工具链

---

## 4. 规则优先，AI 兜底

**日期**: V0.5  
**决策**: 本地规则解析优先，仅复杂/多意图输入调用 AI  
**理由**: 减少 AI 成本、保护隐私、提高响应速度  
**替代方案**: 全部走 AI（拒绝：成本高、隐私风险）

---

## 5. 金额始终 Decimal

**日期**: V0.1  
**决策**: 所有金额字段使用 `DecimalField`，禁止 `float`  
**理由**: 浮点数精度问题导致财务计算错误

---

## 6. 软删除

**日期**: V0.2  
**决策**: Expense/Task/Note 使用 `is_deleted` + `deleted_at` 软删除  
**理由**: 数据可恢复、审计可追溯  
**替代方案**: 物理删除 + 日志（拒绝：恢复困难）

---

## 7. 无 Celery/Redis

**日期**: V0.4  
**决策**: 定时任务使用系统 cron，不引入 Celery  
**理由**: 个人项目任务量小，cron 够用  
**替代方案**: Celery + Redis（拒绝：过度工程）

---

## 8. 站内通知兜底

**日期**: V0.6  
**决策**: Web Push 和邮件作为附加通道，站内通知始终可用  
**理由**: Push 需要浏览器授权、邮件需要 SMTP 配置，站内通知零依赖

---

## 9. Entry 模型过渡

**日期**: V0.2  
**决策**: V0.1 的 Entry 模型保留，新增 Expense/Task/Note，逐步迁移  
**状态**: Entry 仍存在（mark deprecated），V1.1 考虑移除

---

## 10. 不引入前端构建工具

**日期**: V0.1-ongoing  
**决策**: 坚持 CDN 引入 Bootstrap/Chart.js，不使用 npm/webpack  
**理由**: 零构建步骤，部署简单

---

## 待决策

| 议题 | 状态 | 候选方案 |
|------|------|----------|
| V1.1 是否移除 Entry | 待定 | 直接删除 vs 保留 deprecated |
| V2.0 是否拆 App 到独立模块 | 待定 | 当前 life/ 16 模型 vs 拆分到 finance/planning/ |
| 是否加 TOTP 2FA | 待定 | django-otp |
