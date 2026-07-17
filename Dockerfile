# grok-auto-register cloud image (feature/web-panel-goproxy)
# Starts web panel via main.py

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    PANEL_HOST=0.0.0.0 \
    PANEL_PORT=8787 \
    PORT=8787 \
    GOPROXY_ENABLED=0 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    chromium \
    chromium-driver \
    fonts-liberation \
    fonts-noto-cjk \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libxshmfence1 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs /app/cpa_auths /app/data /app/.browser_profiles \
    && if [ ! -f /app/config.json ] && [ -f /app/config.example.json ]; then \
         cp /app/config.example.json /app/config.json; \
       fi

EXPOSE 8787

CMD ["python", "main.py"]