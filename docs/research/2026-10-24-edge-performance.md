# Edge performance, resilience and abuse protection — research

Retrieved: 2026-10-24 (all sources below retrieved this date). Facts marked *(inference)* are recommendations, not sourced claims.

## 1. Caddy 2

- **Rate limiting is not built in.** Third-party module `github.com/mholt/caddy-ratelimit`; README: "This is not an official repository of the Caddy Web Server organization." Build: `xcaddy build --with github.com/mholt/caddy-ratelimit`. Directive is ordered before `basic_auth` by default; override with global `order` or use inside `route`. — https://github.com/mholt/caddy-ratelimit
- Per-IP syntax:
  ```
  rate_limit {
      zone per_ip {
          key    {remote_host}
          events 100
          window 1m
      }
  }
  ```
  If a CDN sits in front, key on `{client_ip}` with `trusted_proxies` configured *(inference; `{remote_host}` would be the CDN IP)*.
- **Docker build:** official image documents a two-stage builder pattern (xcaddy in `caddy:<ver>-builder`, copy binary into `caddy:<ver>`). — https://hub.docker.com/_/caddy
  ```dockerfile
  FROM caddy:2-builder AS builder
  RUN xcaddy build --with github.com/mholt/caddy-ratelimit
  FROM caddy:2
  COPY --from=builder /usr/bin/caddy /usr/bin/caddy
  ```
- **HTTP/3:** publish `-p 443:443 -p 443:443/udp`; `NET_ADMIN` optional, improves UDP buffers. Also open UDP 443 in Hostinger's hPanel firewall. — https://hub.docker.com/_/caddy
- **Precompressed:** `file_server { precompressed }` defaults to `br zstd gzip`; looks for `.br`, `.zst`, `.gz` sidecars. — https://caddyserver.com/docs/caddyfile/directives/file_server. Generate sidecars at Vite build time (e.g. vite-plugin-compression) *(inference)*; keep `encode zstd gzip` for dynamic JSON.
- **Immutable caching:** `@hashed path /assets/*` → `header @hashed Cache-Control "public, max-age=31536000, immutable"`; `index.html` → `no-cache` *(standard practice, inference)*.
- **reverse_proxy** — https://caddyserver.com/docs/caddyfile/directives/reverse_proxy:
  - `lb_policy` (random, round_robin, least_conn, first, …); `lb_try_duration` = how long to retry picking a backend if the next is down.
  - Active: `health_uri`, `health_interval` (default 30s). Passive: `fail_duration` (>0 enables), `max_fails`, `unhealthy_status`.
  - `flush_interval -1` = low-latency mode, flush immediately (SSE). `stream_timeout` force-closes streams after a duration. `transport http { dial_timeout 3s (default) response_header_timeout … }`.
  - Failover: `lb_policy first` + health checks gives primary/secondary *(inference from policy list)*.
- `handle_errors` can serve a static fallback page (e.g. "API offline") when the home-lab upstream is unreachable (502/503) *(Caddy docs; pattern in sketch below)*.

## 2. Cloudflare free in front?

- **Partial (CNAME) setup is Business/Enterprise only**: "A CNAME setup (partial) is only available to customers on a Business or Enterprise plan." Free requires nameserver change. — https://developers.cloudflare.com/dns/zone-setups/partial-setup/
- **Timeout: 125 s, not 100 s.** Current docs: Proxy Read Timeout 125 seconds, configurable only for Enterprise (up to 6000 s); 524 if origin sends nothing in that window. — https://developers.cloudflare.com/fundamentals/reference/connection-limits/ , https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/ . Many blogs still say 100 s.
- **SSE:** streamed, uncached (cf-cache-status DYNAMIC) per third-party reports; Cloudflare's docs do not explicitly document SSE. Send heartbeats (comment line) more often than the timeout. — https://stackharbor.com/en/knowledge-base/cffix-websockets-sse-behind-cloudflare/ , https://levelup.gitconnected.com/the-125-second-silence-why-your-ai-stream-dies-behind-cloudflare-40f776d9e055
- **Default cache:** JS/CSS cached by extension; HTML and JSON not cached by default. — https://developers.cloudflare.com/cache/concepts/default-cache-behavior/
- **Smart Tiered Cache available on Free.** — https://developers.cloudflare.com/cache/how-to/tiered-cache/
- HTTP/3 and Brotli are available on all plans (Cloudflare docs; not re-fetched this session — verify).
- **ToS:** 2023-05-16 update removed the old 2.8 "HTML vs non-HTML" construct; it moved to CDN Service-Specific Terms. Restriction now targets video and large files hosted outside Cloudflare. An HTML+JS SPA is fine. — https://blog.cloudflare.com/updated-tos/
- India PoPs: not verified this session.
- **Bunny CDN:** EU/NA $0.01/GB, Asia/Oceania $0.03/GB, $1 monthly minimum, no request fees. — https://bunny.net/pricing/cdn/
- **Recommendation: Yes, Cloudflare Free (proxied)** for hashed assets + landing page: global edge cache fixes Mumbai-origin latency for US/EU, hides the VPS IP, absorbs volumetric abuse, $0. Cost: nameserver move. Put the SSE API on the same host with heartbeats <60 s, or on a DNS-only subdomain if streams can be silent >125 s.

## 3. Vite / bundle

- Current Vite docs: chunking is configured via `build.rolldownOptions.output.codeSplitting` (Rolldown); dynamic imports auto-split. **The older `rollupOptions.output.manualChunks` advice is outdated for Rolldown-based Vite.** — https://vite.dev/guide/build
- Route split with `React.lazy(() => import('./routes/X'))` + `Suspense`; analyse with rollup-plugin-visualizer *(standard practice)*.
- **Grommet 2.58.0** (npm registry): ships ES modules (`module: es6/index.js`), `sideEffects` limited to ThemeContext files → tree-shakeable in principle; requires styled-components (peer). Unpacked package 11 MB (not bundle size). — https://registry.npmjs.org/grommet/latest . styled-components runtime adds weight and runtime CSS cost *(inference)*. No official bundle-size figure found.
- **Core Web Vitals (p75, mobile and desktop):** LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1. — https://web.dev/articles/vitals
- Target *(inference)*: landing page static HTML with no JS dependency for LCP; SPA initial JS < ~150 KB gzip.

## 4. Hostinger KVM 4

- 4 vCPU, 16 GB RAM, 200 GB NVMe, 16 TB bandwidth, 1 Gbps; $12.99/mo promo, $28.99 renewal (2-yr). "Wanguard DDoS filtering", integrated firewall, Docker manager. — https://www.hostinger.com/vps-hosting
- hPanel managed (cloud) firewall: VPS → Security → Firewall. — https://www.hostinger.com/support/8172641-how-to-use-a-managed-vps-firewall-at-hostinger/
- Anti-DDoS: filters, or **null-routes the IP** in severe attacks (site goes dark) — https://www.hostinger.com/support/what-are-anti-ddos-events-in-hostinger-vps/
- VPN is supported on VPS (OpenVPN template). — https://www.hostinger.com/support/?p=1507 . No Docker/WireGuard prohibition found; AUP not located — verify.

## 5. Uptime monitoring

- UptimeRobot Free: 50 monitors, 5-min interval, 1 status page, "Good for hobby and non-profit projects". — https://uptimerobot.com/pricing/
- Uptime Kuma (MIT, self-hosted): 20 s checks, 90+ notifiers, multiple status pages on custom domains; `louislam/uptime-kuma:2`. — https://github.com/louislam/uptime-kuma . Do not host it only on the VPS it monitors *(inference)*; use UptimeRobot as external watcher of the VPS + Kuma for home-lab.

## 6. Recommended edge architecture *(inference, built on facts above)*

1. Cloudflare Free proxy in front (NS change), SSL Full (strict), Smart Tiered Cache on.
2. Cache rule: `/assets/*` cache everything, edge TTL long; HTML bypass/short.
3. Caddy on VPS serves landing + SPA statics from local disk — never from home lab — so the site is up when the home lab is down.
4. Vite emits hashed files + `.br`/`.gz` sidecars; Caddy `precompressed`, immutable headers.
5. Split SPA by route; aim for small entry chunk; measure with visualizer.
6. `/api/*` → reverse_proxy over NetBird with active+passive health checks, short dial timeout.
7. Optional second upstream (a minimal API on the VPS) with `lb_policy first`.
8. `handle_errors` 502/503/504 → static JSON/HTML "backend offline" for API; SPA shows degraded state.
9. SSE: `flush_interval -1`, heartbeat every 30 s, client auto-reconnect.
10. caddy-ratelimit per real client IP (`{client_ip}` with Cloudflare `trusted_proxies`); hPanel firewall allows 80/443 TCP+UDP only from Cloudflare ranges + SSH from admin.
11. UptimeRobot external checks + public status page; Kuma for internals.

```caddyfile
{
    servers {
        trusted_proxies static 173.245.48.0/20 103.21.244.0/22 # ...full Cloudflare list
        client_ip_headers CF-Connecting-IP
    }
}
example.com {
    root * /srv/site
    @api path /api/*
    handle @api {
        rate_limit {
            zone api_ip {
                key    {client_ip}
                events 60
                window 1m
            }
        }
        reverse_proxy 100.64.0.10:8000 127.0.0.1:8001 {
            lb_policy first
            lb_try_duration 2s
            health_uri /api/health
            health_interval 15s
            fail_duration 30s
            max_fails 2
            unhealthy_status 5xx
            flush_interval -1
            transport http {
                dial_timeout 2s
            }
        }
    }
    handle {
        @hashed path /assets/*
        header @hashed Cache-Control "public, max-age=31536000, immutable"
        header /index.html Cache-Control "no-cache"
        encode zstd gzip
        try_files {path} /index.html
        file_server { precompressed br zstd gzip }
    }
    handle_errors 502 503 504 {
        @apierr path /api/*
        respond @apierr `{"error":"backend_offline"}` 503
        rewrite * /offline.html
        file_server
    }
}
```
Note: Cloudflare IP ranges at https://www.cloudflare.com/ips/ (not fetched this session).
