# Personal Life OS

一个面向个人使用的生活管理系统：通过语音或自然语言快速记录消费、待办与想法，确认后保存，并在“今日”页面集中展示。

## 当前 MVP

- 浏览器语音输入（Chrome / Edge）
- 规则优先的自动解析：支出、待办、随心记
- 自动分类：餐饮、交通、住房、生活缴费、购物、其他
- 用户确认后保存，避免误记
- 今日待办、本月支出与最近记录看板

## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py makemigrations life
python manage.py migrate
python manage.py runserver
```

打开 <http://127.0.0.1:8000>。开发期未配置 MySQL 时将使用 SQLite；配置 `.env` 的 MySQL 参数后会自动切换。

## 工程原则

- 原始输入、解析草稿和最终业务记录分离。
- AI 或规则只生成草稿，用户确认后才写数据库。
- 金额使用 `Decimal`，不使用浮点数。
- `.env` 绝不提交到 Git。

## 下一迭代

1. 登录与用户级数据隔离
2. 周期账单与提醒通知
3. MySQL 正式环境、自动备份和数据导出
4. DeepSeek 复杂语句解析（本地规则无法覆盖时才调用）
5. 周/月复盘与预算预警
