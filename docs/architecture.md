# Personal Life OS — 系统架构

## V0.1 架构

```
┌─────────────────────────────────────────────────────┐
│                    浏览器                            │
│  ┌───────────────────────────────────────────────┐  │
│  │  home.html (Django Template + Bootstrap 5)     │  │
│  │  · 语音输入 (Web Speech API)                   │  │
│  │  · 文本输入                                    │  │
│  │  · 草稿确认卡片                                 │  │
│  │  · 今日看板 (待办 / 支出 / 最近记录)            │  │
│  └───────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP POST
                       ▼
┌─────────────────────────────────────────────────────┐
│                  Django 5 (config/)                  │
│  ┌───────────────────────────────────────────────┐  │
│  │  life/ (单 App)                               │  │
│  │  · parser.py    — 规则解析引擎                 │  │
│  │  · views.py     — home / parse / save          │  │
│  │  · models.py    — Entry 模型                   │  │
│  │  · admin.py     — Django Admin                │  │
│  └───────────────────────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│  ┌───────────────────────────────────────────────┐  │
│  │  SQLite (开发) / MySQL (生产)                  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### V0.1 特点

- **单用户原型**：无登录、无用户表，所有数据为全局共享
- **单一 App**：`life/` 承载所有业务
- **单一模型**：`Entry` 表通过 `kind` 字段区分支出/待办/随心记
- **规则解析**：纯本地关键词匹配，不依赖外部 API
- **确认流程**：解析 → 草稿 → 用户确认 → 写入

---

## V0.2 目标架构

```
┌─────────────────────────────────────────────────────┐
│                    浏览器                            │
│  ┌───────────────────────────────────────────────┐  │
│  │  Django Templates + Bootstrap 5               │  │
│  │  · 注册 / 登录 / 登出                         │  │
│  │  · 语音/文本输入 (同 V0.1)                     │  │
│  │  · 个人今日看板                                │  │
│  └───────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP
                       ▼
┌─────────────────────────────────────────────────────┐
│                  Django 5                            │
│  ┌───────────────────────────────────────────────┐  │
│  │  accounts/ (V0.2 已实现)                      │  │
│  │  · UserProfile · 注册/登录/登出 · 个人设置   │  │
│  └───────────────────────────────────────────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ finance/ │ │planning/ │ │  notes/  │  ← 骨架   │
│  │ (V0.3)   │ │ (V0.4)   │ │ (V0.4)   │            │
│  └──────────┘ └──────────┘ └──────────┘            │
│  ┌──────────┐ ┌──────────┐                         │
│  │ capture/ │ │ common/  │            ← 骨架        │
│  │ (V0.5)   │ │  工具    │                         │
│  └──────────┘ └──────────┘                         │
│  ┌───────────────────────────────────────────────┐  │
│  │  life/ (V0.1 代码保留)                         │  │
│  │  · Entry 模型 · parser · home/parse/save      │  │
│  └───────────────────────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│  ┌───────────────────────────────────────────────┐  │
│  │  SQLite (开发) / MySQL (生产)                  │  │
│  │  · auth_user · accounts_userprofile           │  │
│  │  · life_entry                                  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘

CI/CD: GitHub Actions（自动运行测试）
```

### V0.2 关键变化

| 维度 | V0.1 | V0.2 |
|------|------|------|
| 用户系统 | 无 | Django auth + UserProfile |
| 数据隔离 | 无（全局） | request.user 过滤 |
| 数据模型 | 1 张 Entry 表 | Expense / Task / Note 三表拆分 |
| App 结构 | 单 life | 6 模块：accounts/finance/planning/notes/capture/common |
| CI/CD | 无 | GitHub Actions 自动测试 |
| 安全 | DEBUG=True | 登录保护、CSRF、用户数据隔离 |

---

## 技术栈总览

| 层 | 技术 | 版本/说明 |
|----|------|-----------|
| 后端框架 | Django | 5.x |
| 开发数据库 | SQLite | 本地开发默认 |
| 生产数据库 | MySQL | 通过 .env 切换 |
| 前端 | Django Templates + Bootstrap | 5.3 CDN |
| 图表 | Chart.js | 后续版本引入 |
| AI 解析 | DeepSeek API | V0.5 引入，仅复杂语句 |
| 定时任务 | APScheduler | V0.4 引入 |
| 部署 | Nginx + Gunicorn | V0.8 |
| CI/CD | GitHub Actions | V0.2 引入 |

## 明确不引入的技术

以下技术**明确排除**，除非后续版本有充分理由重新评估：

- React / Vue / 前端框架（保持 Django Templates）
- Redis / Kafka / Celery（初期用 APScheduler）
- Docker / Kubernetes（保持裸机部署简单性）
- 微服务架构（保持单体 Django）
- 原生移动端 App（使用 PWA，V0.6）

---

## 目录结构（V0.2 当前状态）

```
personal-life-os/
├── .env.example
├── .gitignore
├── README.md
├── PROJECT_RULES.md
├── requirements.txt
├── manage.py
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── accounts/                  ← V0.2 已实现
│   ├── models.py              ← UserProfile
│   ├── signals.py             ← post_save 自动创建 Profile
│   ├── views.py               ← 注册 / 登录 / 登出 / 个人设置
│   ├── urls.py
│   ├── apps.py
│   ├── tests.py
│   ├── migrations/
│   └── templates/accounts/
├── finance/                   ← V0.2 已注册（V0.3 实现）
│   ├── apps.py / urls.py / views.py / models.py / tests.py
├── planning/                  ← V0.2 已注册（V0.4 实现）
│   ├── apps.py / urls.py / views.py / models.py / tests.py
├── notes/                     ← V0.2 已注册（V0.4 实现）
│   ├── apps.py / urls.py / views.py / models.py / tests.py
├── capture/                   ← V0.2 已注册（V0.5 实现）
│   ├── apps.py / urls.py / views.py / models.py / tests.py
├── common/                    ← V0.2 已注册（通用工具）
│   ├── apps.py / urls.py / views.py / models.py / tests.py
├── life/                      ← V0.1 代码保留（V0.3~V0.5 逐步迁移）
│   ├── models.py              ← Entry（待拆分为 Expense/Task/Note）
│   ├── parser.py              ← 规则解析引擎（待迁至 capture/）
│   ├── views.py               ← home / parse / save
│   ├── urls.py
│   ├── admin.py
│   ├── tests.py
│   ├── migrations/
│   └── templates/life/
├── docs/
│   ├── ROADMAP.md
│   ├── architecture.md
│   ├── data-model.md
│   ├── v0.2-requirements.md
│   ├── v0.2-acceptance.md
│   └── testing.md
└── .github/
    └── workflows/              ← V0.2-07 配置
```