# Personal Life OS — 部署指南

## 架构

```
Browser ──→ Nginx(:443) ──→ Gunicorn(:8000) ──→ Django ──→ MySQL
                  │                    │
            /static/              systemd(lifeos)
            Let's Encrypt         健康检查(/health/)
```

## 1. 服务器准备

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y nginx mysql-server python3.12-venv

# 创建目录并设置权限（www-data 是 systemd 运行用户）
sudo mkdir -p /opt/lifeos
sudo chown -R www-data:www-data /opt/lifeos
sudo mkdir -p /opt/lifeos/logs /opt/lifeos/backups
sudo chown www-data:www-data /opt/lifeos/logs /opt/lifeos/backups
sudo chmod 755 /opt/lifeos/logs /opt/lifeos/backups
```

## 2. 部署代码

```bash
cd /opt/lifeos
git clone https://github.com/your-user/personal-life-os.git .
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn

# 配置 .env
cp .env.example .env
nano .env  # 填入 DJANGO_SECRET_KEY, MYSQL_*, DJANGO_ENVIRONMENT=production
```

## 3. MySQL

```sql
CREATE DATABASE personal_life_os CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'lifeos'@'127.0.0.1' IDENTIFIED BY 'strong-password';
GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,INDEX,ALTER ON personal_life_os.* TO 'lifeos'@'127.0.0.1';
```

## 4. 初始化

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py check --deploy
python manage.py verify_db
```

## 5. Gunicorn + systemd

```bash
sudo cp deploy/lifeos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lifeos
sudo systemctl start lifeos
sudo systemctl status lifeos
```

### 反向代理配置

Nginx 传递 `X-Forwarded-Proto` header，Django 通过 `SECURE_PROXY_SSL_HEADER` 信任该头。
生产环境 `ENVIRONMENT=production` 时自动启用，避免 HTTPS 重定向循环。

## 6. Nginx + HTTPS

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/lifeos
sudo ln -s /etc/nginx/sites-available/lifeos /etc/nginx/sites-enabled/
# 修改 server_name 为你的域名

sudo certbot --nginx -d your-domain.com  # Let's Encrypt
sudo systemctl reload nginx
```

## 7. 定时任务

```bash
crontab -e
```

```
# 每日提醒扫描（早上 8 点）
0 8 * * * cd /opt/lifeos && .venv/bin/python manage.py scan_reminders >> logs/cron.log 2>&1

# 每日备份（凌晨 2 点）
0 2 * * * cd /opt/lifeos && .venv/bin/python manage.py backup_db >> logs/cron.log 2>&1

# 数据质量检查（每周日）
0 3 * * 0 cd /opt/lifeos && .venv/bin/python manage.py data_check >> logs/cron.log 2>&1
```

## 8. 验证

```bash
# 健康检查
curl https://your-domain.com/health/
# → {"status":"ok","database":"ok","version":"0.8.0"}

# 测试
python manage.py test

# 日志
tail -f logs/app.log logs/error.log logs/gunicorn-error.log
```

## 9. 更新

```bash
cd /opt/lifeos
git pull
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl reload lifeos
```

## 10. 监控

| 检查项 | 命令/URL |
|--------|----------|
| 进程 | `systemctl status lifeos` |
| 健康 | `GET /health/` |
| 日志 | `journalctl -u lifeos -f` |
| 磁盘 | `df -h` |
| 备份 | `python manage.py backup_db --list` |
