# 部署到 Railway（公开访问 · 自动部署）

适合：让别人通过浏览器直接打开使用，且 `git push` 即自动重新部署。
前置：代码已推到 GitHub（本机 `git push` 后 Railway 自动拉取）。

## 1. 新建项目
Railway 控制台 → **New Project** → **Deploy from GitHub repo** → 选择 `personal-life-os`。
Railway 检测到 `Dockerfile`，自动用容器方式构建（无需 Procfile）。

## 2. 配置环境变量（Variables）
在 Project → Variables 添加：

| 变量 | 值 | 说明 |
|---|---|---|
| `DJANGO_ENVIRONMENT` | `production` | 启用 HSTS / 安全 Cookie / 密码强度 |
| `DJANGO_DEBUG` | `False` | 生产必须关 |
| `DJANGO_SECRET_KEY` | 一段 ≥50 位随机串 | 可用 `python -c "import secrets;print(secrets.token_urlsafe(50))"` 生成 |
| `DJANGO_ALLOWED_HOSTS` | `你的域名,*.railway.app` | 如 `lifeos.up.railway.app`，多个逗号分隔 |
| `MYSQLDATABASE` / `MYSQLUSER` / `MYSQLPASSWORD` / `MYSQLHOST` / `MYSQLPORT` | （接插件时自动注入） | 见下方「数据库持久化」 |

> 不填数据库变量 → 默认用 SQLite，开箱即用。

## 3. 数据库持久化（推荐，避免重启丢数据）
Railway 文件系统是临时的，重新部署会清空 SQLite。二选一：
- **方案 A（推荐）**：Add → Database → MySQL，Railway 自动注入 `MYSQL*` 变量，本项目会**自动切换 MySQL**（settings 已兼容）。首次部署后执行一次迁移（见第 5 步）。
- **方案 B**：挂一个 Volume 到 `/app/data`，并在 Variables 加 `MYSQL_DATABASE=` 保持为空让 SQLite 落盘到该卷（需自行确保路径）。

## 4. 生成域名
Settings → Networking → Generate Domain，得到 `https://xxx.up.railway.app`。
把该域名填回 `DJANGO_ALLOWED_HOSTS`。

## 5. 首次初始化
Railway 构建时已自动 `collectstatic`。仍需手动执行一次迁移与超级管理员：
- 进入项目 Shell（或本地 `railway run`）：
  ```bash
  python manage.py migrate
  python manage.py createsuperuser
  ```
- 想预置 demo 数据：`python manage.py seed_demo`

## 6. 迭代更新
`git push` → Railway 自动重新构建部署。**注意**：改动 `requirements.txt` 或静态资源后 Railway 会重新 `collectstatic`，无需手动操作。

## 故障排查
- 白屏 / 502：看 Deploy Logs，常见是 `DJANGO_SECRET_KEY` 未设（生产模式会 `RuntimeError` 拒绝启动）或 `DJANGO_ALLOWED_HOSTS` 没包含当前域名（返回 400 Bad Request）。
- 页面无样式：确认 `whitenoise` 已在 `requirements.txt`（已加）且 `collectstatic` 成功（构建日志应出现 `staticfiles/`）。
- 限流在单 worker 下正常；多 worker 请加 `REDIS_URL` 环境变量。
