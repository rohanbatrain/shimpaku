# Public read-only replay of Shimpaku (see deploy/vps/README.md).
# No Junos libraries, no device credentials, no LLM key: this image cannot reach a switch.

FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SHIMPAKU_BIND=0.0.0.0 SHIMPAKU_PORT=8780 SHIMPAKU_AUTH_MODE=off \
    SHIMPAKU_DATA=/tmp/shimpaku SHIMPAKU_REPLAY=/snapshot/snapshot.json
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY bench/virga/ bench/virga/
# editable: the API serves web/dist relative to the source tree
RUN pip install -e . && useradd --system --uid 10001 --home /nonexistent shimpaku
COPY --from=web /src/web/dist web/dist
USER 10001
EXPOSE 8780
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8780/api/whoami').status != 200)"
CMD ["python", "-m", "shimpaku.api"]
