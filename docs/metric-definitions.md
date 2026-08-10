# Personal Life OS — 指标口径定义

> 版本：V0.7-01 | 所有指标统计范围为当前用户，所有金额字段类型为 Decimal。

---

## 1. 本月支出

| 项 | 说明 |
|----|------|
| 数据来源 | `Expense` 表 |
| 计算公式 | `SUM(amount) WHERE user=当前用户 AND type='expense' AND status='confirmed' AND is_deleted=false AND occurred_at 在本月` |
| 边界情况 | pending/voided 不计入；软删除不计入；跨月时按 `occurred_at` 所在月归属 |
| 示例 | 8 月 5 日记了一笔 confirmed 支出 ¥50，8 月 15 日记了一笔 pending 支出 ¥100 → 本月支出 = ¥50 |

---

## 2. 本月收入

| 项 | 说明 |
|----|------|
| 数据来源 | `Expense` 表 |
| 计算公式 | `SUM(amount) WHERE user=当前用户 AND type='income' AND status='confirmed' AND is_deleted=false AND occurred_at 在本月` |
| 边界情况 | 同支出规则；仅 `type='income'` |
| 示例 | 8 月 1 日记工资 ¥5000(confirmed)、8 月 10 日记退款 ¥200(pending) → 本月收入 = ¥5000 |

---

## 3. 月度结余

| 项 | 说明 |
|----|------|
| 数据来源 | 本月收入 + 本月支出 |
| 计算公式 | `本月收入 - 本月支出` |
| 边界情况 | 正数=盈余，负数=赤字；当月无任何记录 → 0 |
| 示例 | 收入 ¥5000 - 支出 ¥3200 = 结余 ¥1800 |

---

## 4. 预算执行率

| 项 | 说明 |
|----|------|
| 数据来源 | `Budget`(amount) + `Expense`(spent) |
| 计算公式 | `本月支出 / Budget.amount × 100%`（本月 Budget.category IS NULL） |
| 边界情况 | 未设置预算 → "未设置"；超过 100% → 显示 100%+ 并标红超支；月初 1 号支出为 0 → 执行率 0% |
| 示例 | 预算 ¥5000，已花 ¥3500 → 执行率 70% |

---

## 5. 固定支出占比

| 项 | 说明 |
|----|------|
| 数据来源 | `RecurringExpense`(活跃的固定支出总和) + `Expense`(本月总支出) |
| 计算公式 | `SUM(RecurringExpense.amount WHERE is_active=true) / 本月支出 × 100%` |
| 边界情况 | 本月支出为 0 → "—"；固定支出指预计值，非实际扣款 |
| 示例 | 房租 ¥3000 + 话费 ¥100 + 保险 ¥500 = 固定 ¥3600；本月支出 ¥6000 → 60% |

---

## 6. 日均支出

| 项 | 说明 |
|----|------|
| 数据来源 | `Expense` 表 |
| 计算公式 | `本月支出 / 本月已过天数`（含今天） |
| 边界情况 | 月初第 1 天 → = 本月支出；月中某天无消费 → 仍计入分母 |
| 示例 | 8 月 11 日，本月支出 ¥2200 → 日均 = ¥2200 / 11 = ¥200 |

---

## 7. 分类消费占比

| 项 | 说明 |
|----|------|
| 数据来源 | `Expense` + `Category` |
| 计算公式 | 按 `category` 分组：`SUM(amount) / 本月支出 × 100%` |
| 边界情况 | 无分类(None) → "未分类"；仅统计 confirmed 支出；占比四舍五入到整数 |
| 示例 | 餐饮 ¥1000(33%)、交通 ¥500(17%)、购物 ¥1500(50%) |

---

## 8. 任务完成率

| 项 | 说明 |
|----|------|
| 数据来源 | `Task` 表 |
| 计算公式 | `COUNT(status='completed' AND completed_at 在本月) / COUNT(created_at 在本月 OR completed_at 在本月) × 100%` |
| 边界情况 | 分子 = 本月完成的任务(无论何时创建)；分母 = 本月创建或本月完成的任务(去重)；分母为 0 → "—" |
| 示例 | 本月创建 10 个任务，完成 7 个 → 70% |

---

## 9. 逾期率

| 项 | 说明 |
|----|------|
| 数据来源 | `Task` 表 |
| 计算公式 | `COUNT(status IN ('todo','in_progress') AND due_at < 今天) / COUNT(status IN ('todo','in_progress','completed') AND is_deleted=false) × 100%` |
| 边界情况 | 无截止日期的任务不参与计算；cancelled/archived 不计入分母；分母为 0 → 0% |
| 示例 | 10 个活跃任务中 2 个 overdue → 逾期率 20% |

---

## 10. 连续完成天数

| 项 | 说明 |
|----|------|
| 数据来源 | `Task` 表 `completed_at` |
| 计算公式 | 从今天往回数，每一天至少有一个 `completed_at` 在该日期的任务 → 连续天数 |
| 边界情况 | 今天尚未完成任何任务 → 从昨天开始数；中断后计数归零；只统计当前用户 |
| 示例 | 8/10→完成 1 个，8/9→完成 2 个，8/8→0 个 → 连续 2 天 |

---

## 通用规则

- **用户隔离**：所有统计必须 `WHERE user=request.user`
- **金额类型**：全部使用 `Decimal`，不得使用 `float`
- **时区**：用户设置时区 > 系统默认 `Asia/Shanghai`
- **确认状态**：除特别说明外，仅统计 `status='confirmed'` 的支出
- **软删除**：`is_deleted=true` 的记录一律不计入统计
