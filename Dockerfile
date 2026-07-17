# grok-auto-register cloud image (feature/web-panel-goproxy)
# Multi-stage: prebuild GoProxy binary, then run web panel via main.py

# ---------- stage 1: build GoProxy (linux) ----------
FROM golang:1.25-bookworm AS goproxy-builder

WORKDIR /src
COPY third_party/goproxy/go.mod third_party/goproxy/go.sum ./
RUN go mod download

COPY third_party/goproxy/ ./
# Must use pure-Go sqlite (modernc.org/sqlite). Do NOT build with mattn/go-sqlite3 under CGO_ENABLED=0.
ENV CGO_ENABLED=0
RUN mkdir -p /out/bin \
    && go build -trimpath -ldflags="-s -w" -o /out/bin/proxygo . \
    && chmod +x /out/bin/proxygo

# Optional sing-box for custom encrypted nodes (not required for basic pool mode).
ARG SINGBOX_VERSION=1.13.5
RUN ARCH=$(case "$(dpkg --print-architecture)" in amd64) echo "amd64";; arm64) echo "arm64";; *) echo "amd64";; esac) \
    && curl -fsSL "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-linux-${ARCH}.tar.gz" \
       -o /tmp/sing-box.tar.gz \
    && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
    && cp "/tmp/sing-box-${SINGBOX_VERSION}-linux-${ARCH}/sing-box" /out/bin/sing-box \
    && chmod +x /out/bin/sing-box \
    && rm -rf /tmp/sing-box*

# ---------- stage 2: runtime ----------
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
    DISPLAY=:99 \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

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
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Prebuilt GoProxy + sing-box into image (no Go toolchain needed at runtime).
RUN mkdir -p /app/third_party/goproxy/bin /app/config /app/logs /app/cpa_auths /app/data /app/.browser_profiles
COPY --from=goproxy-builder /out/bin/proxygo /app/third_party/goproxy/bin/proxygo
COPY --from=goproxy-builder /out/bin/sing-box /usr/local/bin/sing-box
RUN chmod +x /app/third_party/goproxy/bin/proxygo /usr/local/bin/sing-box \
    && mkdir -p /app/data/goproxy /app/logs /app/cpa_auths \
    && test -f /app/turnstilePatch/manifest.json \
    && test -f /app/turnstilePatch/script.js \
    && ln -sf /app/third_party/goproxy/bin/proxygo /app/third_party/goproxy/bin/proxy-pool \
    && if head -c 2 /app/third_party/goproxy/bin/proxygo | grep -q MZ; then echo 'ERROR: proxygo is Windows PE; expected Linux ELF' >&2; exit 1; fi \
    && if [ ! -f /app/config/config.json ]; then \
         if [ -f /app/config/config.example.json ]; then \
           cp /app/config/config.example.json /app/config/config.json; \
         elif [ -f /app/config.example.json ]; then \
           cp /app/config.example.json /app/config/config.json; \
         else \
           echo '{}' > /app/config/config.json; \
         fi; \
       fi

EXPOSE 8787

CMD ["python", "main.py"]
