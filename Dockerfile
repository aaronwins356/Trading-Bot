# ---- dashboard build
FROM node:22-alpine AS ui
WORKDIR /ui
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---- runtime
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TRADEBOT_DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=ui /ui/dist ./src/tradebot/api/static
RUN pip install . && useradd --create-home --uid 1000 bot && mkdir -p /data && chown bot /data
COPY research ./research
COPY config/config.example.yaml ./config/config.example.yaml
USER bot
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
CMD ["tradebot", "serve", "--host", "0.0.0.0", "--port", "8080"]
