# Personal Life OS — 数据模型

## V0.1 数据模型（当前）

### ER 图

```
┌──────────────────────────────────────────────────┐
│                    Entry                          │
├──────────────────────────────────────────────────┤
│ id            BigAutoField   PK                  │
│ kind          CharField      expense/task/note   │
│ title         CharField(200)                     │
│ raw_text      TextField                          │
│ category      CharField      餐饮/交通/住房/...    │
│ amount        DecimalField   可为空                │
│ occurred_on   DateField      可为空                │
│ due_at        DateTimeField  可为空                │
│ priority      SmallInt       1高 2中 3低           │
│ completed     BooleanField   default=False        │
│ created_at    DateTimeField  auto_now_add         │
└──────────────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    expense       task        note
  (kind 值)    (kind 值)    (kind 值)
```

### V0.1 问题

1. **无用户关联**：所有数据全局共享，无法区分归属
2. **单表混用**：用 `kind` 区分类型，导致大量字段可空、查询需要额外条件
3. **无审计字段**：缺少 `updated_at`，无法追踪修改历史
4. **分类硬编码**：`Category` 为模型 choices，用户不可自定义

---

## V0.2 数据模型（目标）

### ER 图

```
┌──────────────────────┐
│     User (Django)     │
├──────────────────────┤
│ id         PK        │
│ username             │
│ email                │
│ password             │
│ is_active            │
│ date_joined          │
└──────┬───────────────┘
       │ 1
       │
       ├──────────────────────────┐
       │ 1                        │ 1
       ▼                          ▼
┌──────────────┐    ┌────────────────────────────────┐
│ UserProfile  │    │          Expense               │
├──────────────┤    ├────────────────────────────────┤
│ id      PK   │    │ id           PK                │
│ user_id FK───┼───▶│ user_id      FK → User        │
│ timezone     │    │ title        CharField(200)    │
│ created_at   │    │ raw_text     TextField         │
│ updated_at   │    │ category     FK → Category     │
└──────────────┘    │ amount       DecimalField      │
                    │ occurred_on  DateField         │
                    │ created_at    DateTimeField     │
                    │ updated_at    DateTimeField     │
                    └────────────────────────────────┘

       ┌────────────────────────────────┐
       │            Task                │
       ├────────────────────────────────┤
       │ id           PK                │
       │ user_id      FK → User        │
       │ title        CharField(200)    │
       │ raw_text     TextField         │
       │ due_at       DateTimeField     │
       │ priority     SmallInt (1/2/3)  │
       │ completed    BooleanField      │
       │ completed_at DateTimeField     │
       │ created_at   DateTimeField     │
       │ updated_at   DateTimeField     │
       └────────────────────────────────┘

       ┌────────────────────────────────┐
       │            Note                │
       ├────────────────────────────────┤
       │ id           PK                │
       │ user_id      FK → User        │
       │ title        CharField(200)    │
       │ raw_text     TextField         │
       │ occurred_on  DateField         │
       │ created_at   DateTimeField     │
       │ updated_at   DateTimeField     │
       └────────────────────────────────┘

       ┌────────────────────────────────┐
       │          Category              │
       ├────────────────────────────────┤
       │ id           PK                │
       │ user_id      FK → User        │
       │ name         CharField        │
       │ icon         CharField (emoji) │
       │ kind         CharField         │  ← income/expense
       │ is_default   BooleanField      │
       │ created_at   DateTimeField     │
       └────────────────────────────────┘
                    ▲
                    │ FK
                    │
       ┌───────────┴────┐
       │    Expense      │
       └────────────────┘
```

### V0.2 核心表说明

#### Expense（支出/收入）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BigAutoField | 主键 |
| `user` | ForeignKey → User | **V0.2 新增：数据归属** |
| `title` | CharField(200) | 消费项目 |
| `raw_text` | TextField | 原始语音/文本输入 |
| `category` | ForeignKey → Category | 消费分类（可自定义） |
| `amount` | DecimalField | 金额，**必须 Decimal** |
| `occurred_on` | DateField | 消费日期 |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | **V0.2 新增** auto_now |

#### Task（待办）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BigAutoField | 主键 |
| `user` | ForeignKey → User | **V0.2 新增：数据归属** |
| `title` | CharField(200) | 任务内容 |
| `raw_text` | TextField | 原始输入 |
| `due_at` | DateTimeField | 截止时间 |
| `priority` | SmallInteger | 1=高, 2=中, 3=低 |
| `completed` | BooleanField | 是否完成 |
| `completed_at` | DateTimeField | **V0.2 新增** 完成时间 |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | **V0.2 新增** auto_now |

#### Note（随心记）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BigAutoField | 主键 |
| `user` | ForeignKey → User | **V0.2 新增：数据归属** |
| `title` | CharField(200) | 笔记内容 |
| `raw_text` | TextField | 原始输入 |
| `occurred_on` | DateField | 日期 |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | **V0.2 新增** auto_now |

#### Category（分类）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BigAutoField | 主键 |
| `user` | ForeignKey → User | 归属，创建默认分类时 user 可为空表示系统预置 |
| `name` | CharField | 分类名称 |
| `icon` | CharField | emoji 图标 |
| `kind` | CharField | income / expense |
| `is_default` | BooleanField | 是否系统预置 |
| `created_at` | DateTimeField | auto_now_add |

#### UserProfile（用户扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BigAutoField | 主键 |
| `user` | OneToOneField → User | 关联 Django User |
| `timezone` | CharField | 时区，默认 Asia/Shanghai |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

---

## V0.1 → V0.2 迁移策略

1. **V0.1 无用户系统**：V0.2 引入 User 后，现有 Entry 数据无法绑定用户
   - 方案：V0.1 数据量少（开发测试阶段），可直接删除旧数据 + 重建迁移
   - 如有保留需求：迁移脚本将旧 Entry 绑定到首个管理员用户
2. **Entry → Expense/Task/Note 拆分**：原 `Entry` 表按 `kind` 字段拆为三张表
3. **迁移顺序**：User → UserProfile → Category → Expense → Task → Note
4. **回滚风险**：V0.1 到 V0.2 的迁移不可直接回滚（涉及拆表），需提前备份数据

### V0.2 不做（暂不实现）

- Budget 预算表（V0.3）
- RecurringExpense 周期账单（V0.3）
- Reminder 独立提醒表（V0.4）
- ConversationLog AI 对话日志（V0.5）
- NotificationLog 通知日志（V0.6）
- 收入/转账记录（V0.3）
- 自定义提醒规则（V0.4）
- 数据导出（V0.8）
- 审计日志（V0.8）