# Personal Life OS — 容器镜像
# 基于 deploy/gunicorn.conf.py 生产配置；默认单 worker（限流缓存为进程内
# LocMemCache，设置 REDIS_URL 后自动切换为 Redis 跨进程共享）。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# 静态文件收集（由 WhiteNoise 直接托管；收集失败不阻断镜像构建）
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

# Railway / 容器平台通过 $PORT 注入端口，且需监听 0.0.0.0 才能被路由访问。
# 单 worker 保证 LocMemCache 限流计数正确；挂 REDIS_URL 后可加 -w 提升并发。
CMD gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} -w 1
