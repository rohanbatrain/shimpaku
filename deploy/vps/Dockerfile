# Public read-only replay of Shimpaku (see deploy/vps/README.md).
# No Junos libraries, no device credentials, no LLM key: this image cannot reach a switch.

ARG CADDY_VERSION=2.11

FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Edge: Caddy + two plugins, built with the official builder image (xcaddy), then the built SPA
# baked in (hashed assets + .br/.zst/.gz siblings). compose.yaml builds this target for `caddy`;
# the Caddyfile stays a bind mount.
#   mholt/caddy-ratelimit         `rate_limit` (not an official Caddy-org module)
#   WeidiDeng/caddy-cloudflare-ip `trusted_proxies cloudflare` (fetches Cloudflare's published ranges)
# Caddy is pinned to a minor tag; pin the plugins to tags/commits by overriding the ARGs
# (e.g. --build-arg RATELIMIT_REF=<commit>) once a build has been verified. TODO: pin digests.
FROM caddy:${CADDY_VERSION}-builder AS edge-builder
ARG RATELIMIT_REF=latest
ARG CFIP_REF=latest
RUN xcaddy build \
      --with github.com/mholt/caddy-ratelimit@${RATELIMIT_REF} \
      --with github.com/WeidiDeng/caddy-cloudflare-ip@${CFIP_REF}

FROM caddy:${CADDY_VERSION} AS edge
COPY --from=edge-builder /usr/bin/caddy /usr/bin/caddy
COPY --from=web /src/web/dist /srv/app
COPY deploy/vps/offline /srv/offline
COPY deploy/vps/Caddyfile /etc/caddy/Caddyfile

FROM python:3.12-slim AS replay
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
