# Public deployment: landing page, live dashboard, replay fallback

```
visitor ─► Cloudflare (free, proxied) ─► VPS Caddy ─┬─► <domain>: landing (static)
                                                    ├─► <label>-app.<parent>: SPA (static, baked into the edge image)
                                                    ├─► <label>-app.<parent>/api/*, /mcp ─► NetBird ─► lab: shimpakud (live)
                                                    └─► <label>-app.<parent>/api/* reads, lab down ─► replay container
```

| Path on `<label>-app.<parent>` | Lab up | Lab down |
|---|---|---|
| `/`, `/start`, other client routes, `/assets/*` | Caddy, static | Caddy, static |
| `GET /api/*` | live daemon | replay (read-only snapshot) |
| `POST`/`PUT`/`PATCH`/`DELETE /api/*` | live daemon | `503 {"detail":"the lab is offline; the dashboard is showing a recorded replay"}` |
| `/mcp` | live daemon (401 without a token) | `503` JSON; never the replay |

## Failover

`reverse_proxy <lab> replay:8780 { lb_policy first }` for reads: Caddy actively checks
`/api/whoami` on both upstreams every 10 s and passively marks an upstream down for 30 s after
two failures (dial timeout 2 s, retries for up to 5 s). The first healthy upstream wins, so the
lab is used whenever it answers. Writes and `/mcp` go through separate `reverse_proxy` blocks with
**only** the lab upstream; with the lab down they get "no upstreams available"/a dial error, and
`handle_errors` answers 503 JSON. A non-API 502/503/504 gets `offline/index.html`.

Caveats:
- A request that is already streaming (SSE) when the lab dies ends; the client reconnects and
  lands on the replay. There is no fail-back of an open stream when the lab returns.
- A session cookie issued by the live daemon means nothing to the replay: during an outage a
  signed-in operator or tester is an anonymous reader.
- Up to ~10 s after the lab dies (until its health check fails), reads pay the 2 s dial timeout
  before retrying on the replay; writes in that window get 503.
- The edge container reaches the lab through the VPS's NetBird interface (host NAT). If NetBird
  runs in a container or with a userspace netstack, check the route from inside `caddy`.

## Why the replay still exists

The live daemon can change switch configuration; its controls (sign-in, roles, guardrails,
`commit confirmed`) are what make it safe to expose, and the lab host is intermittent. The
replay keeps the site useful when the lab is off: every number in it was recorded from the live
system. It holds no device credentials, no Junos libraries, no LLM key, cannot open a connection
to the lab, refuses writes (`403`), and is never used for `/mcp`.

For a live walkthrough without the edge, use the SSH tunnel in `DEMO.md`.

## What a snapshot contains

`shimpaku snapshot export` (on the lab host) reads every route the dashboard reads and writes one
JSON file. On the way out it drops management addresses, rewrites home paths to `~`, replaces
each `--redact` term (the lab host's name, the service user) with `<redacted>`, and **refuses to
write** if any of those terms survives, or if the live audit chain does not verify. `deploy.sh`
then refuses to ship it if it mentions the ssh names of the lab host or the VPS.

## Static serving and caching

`Dockerfile` builds the SPA once (stage `web`) and copies `web/dist` into two images: the replay
(`target: replay`, which still serves it for direct use) and the edge (`target: edge`: Caddy
built with xcaddy in `caddy:<ver>-builder` plus `mholt/caddy-ratelimit` and
`WeidiDeng/caddy-cloudflare-ip`, with the files at `/srv/app` and the offline page at
`/srv/offline`). The plugin refs are `latest` until pinned with `--build-arg RATELIMIT_REF=`/
`CFIP_REF=`; Caddy is pinned to a minor tag (`CADDY_VERSION`). On `<label>-app.<parent>` Caddy serves the files itself, so the SPA stays up and cacheable whatever
the lab or the replay is doing.

- The build writes `.br`, `.gz` and (Node >= 22.15) `.zst` next to every text asset over 1 KB
  (`web/vite.config.ts`); `file_server precompressed br zstd gzip` sends them with no runtime
  compression cost.
- `/assets/*` (content-hashed): `Cache-Control: public, max-age=31536000, immutable`; a missing
  asset is a 404, never the SPA shell.
- Everything else (`index.html`, client routes via `try_files {path} /index.html`, `mark.svg`):
  `Cache-Control: no-cache`, so a deploy is visible on the next load.
- Static responses carry the same CSP and `X-Frame-Options` the replay sets on its pages.

Pages are code-split per route (`web/src/App.tsx`); recharts loads after first paint.

## Container hardening

`compose.yaml`: read-only root filesystem, `tmpfs` for `/tmp`, all capabilities dropped,
`no-new-privileges`, non-root user (uid 10001), 512 MB memory and 128-process limits, no
published port (only Caddy reaches it). Caddy terminates TLS (automatic certificates), adds HSTS,
and strips its `Server` header; the app sets its own CSP and frame headers.

## Client IP and rate limits

Cloudflare is the only peer Caddy accepts a client address from: `trusted_proxies cloudflare`
(the `caddy-cloudflare-ip` module fetches Cloudflare's published ranges and refreshes them every
12 h) with `client_ip_headers CF-Connecting-IP`. `{client_ip}` is then the visitor, and is what
every limit keys on and what Caddy sends upstream as the single `X-Forwarded-For` value.

| Zone | Matches | Per client IP |
|---|---|---|
| `login` | `POST /api/login`, `POST /api/logout-all` | 10 / 10 min |
| `invites` | `/api/invites/*` | 10 / min |
| `demo` | `/api/demo/*` | 10 / min |
| `actions_write` | `POST /api/actions*` | 20 / min |
| `assistant` | `/api/assistant*` | 10 / min |
| `mcp` | `/mcp*` | 60 / min |
| `api` | all `/api/*` | 300 / min |

Over the limit: `429` with `Retry-After`. The daemon's own per-principal limits
(`SHIMPAKU_RATE_LIMITS`) still apply behind these.

**SSE:** `flush_interval -1`; no read or stream timeout at Caddy; the app pings every 15 s, inside
Cloudflare's 125 s idle limit (a silent origin gets a 524).

## Lab host behind the edge

On the lab host, in `~/.config/shimpaku/env` (the unit no longer hard-codes bind or auth):

```bash
SHIMPAKU_BIND=<lab netbird ip>          # reachable on the NetBird interface only, not the LAN
SHIMPAKU_AUTH_MODE=local                # sign-in required; the code default stays `off`
SHIMPAKU_PUBLIC_URL=https://<label>-app.<parent>   # base for invite links
SHIMPAKU_PUBLIC_VIEW=1                  # anonymous visitors read live, read-only; they request sessions
SHIMPAKU_TRUSTED_PROXIES=<edge netbird ip>
FORWARDED_ALLOW_IPS=<edge netbird ip>   # what uvicorn in shimpakud actually reads today (see below)
```

then, **before** setting `SHIMPAKU_PUBLIC_VIEW=1`, on the lab host:

```bash
shimpaku admin set-password     # argon2id hash in <data>/admin.hash (0600)
shimpaku admin totp enable      # otpauth:// URI (and a QR if qrcode/segno is installed) + 8 recovery codes
shimpaku admin totp status      # → TOTP: enabled; 8 recovery codes left
```

Add the URI to an authenticator app and put the recovery codes in your password manager (not on
this host). With public view on, operator sign-in is refused until TOTP is enabled, and operator
sessions last 30 min idle / 4 h at most (`SHIMPAKU_OPERATOR_IDLE_MIN`, `SHIMPAKU_OPERATOR_MAX_H`).
Then `systemctl --user restart shimpakud`. Restrict the port further with NetBird access policies
(edge peer → lab peer, TCP 8780 only).

Why both proxy variables: `python -m shimpaku.api` (the replay) passes
`forwarded_allow_ips=$SHIMPAKU_TRUSTED_PROXIES` to uvicorn, but `shimpakud` (`daemon.py`) builds
`uvicorn.Config` without it, so uvicorn falls back to `$FORWARDED_ALLOW_IPS` (default
`127.0.0.1`). Without one of them the daemon ignores `X-Forwarded-Proto`/`X-Forwarded-For` from
the edge: it sees `http` (cookies lose `Secure`/`__Host-`) and the edge's address as every
client's address (one shared per-IP budget).

Verify: from the edge, `curl -s http://<lab netbird ip>:8780/api/whoami`; from the LAN, the same
address on another interface must refuse; through the edge, sign in and check the cookie is
`Secure`.

## Cloudflare

Free plan, proxied. The free plan has no partial (CNAME) setup, so the nameservers move.

1. **Add the zone** in Cloudflare, let it import the existing records, then set the two
   Cloudflare nameservers at the registrar. Verify: `dig NS <domain> +short` lists only
   Cloudflare's; the dashboard shows the zone "Active".
2. **DNS:** `A <domain> → <vps ip>` and `A <label>-app.<parent> → <vps ip>` (plus `AAAA` if the VPS has
   IPv6), both **Proxied** (orange cloud). Nothing else points at the VPS. Verify:
   `dig +short <label>-app.<parent>` returns Cloudflare addresses, not the VPS.
3. **SSL/TLS: Full (strict), with Caddy's own public certificates.** Caddy keeps getting
   Let's Encrypt certificates (HTTP-01 over port 80 passes through the proxy; TLS-ALPN works too),
   so the origin is valid for any client, renews itself, and nothing breaks if a record is ever
   set to DNS-only. An Origin CA certificate would also work but is trusted only by Cloudflare and
   is one more 15-year secret to place by hand. Verify: `curl -sI https://<label>-app.<parent>` is 200 and
   `cf-ray` is present; Caddy's log shows certificates obtained for both names.
   Also on: Always Use HTTPS, HTTP/3, Brotli, Smart Tiered Cache.
4. **Cache rule:** hostname `<label>-app.<parent>` and URI path starts with `/assets/` → Eligible for
   cache, Edge TTL "use origin cache-control" (the origin says `immutable`, one year). No rule
   for HTML, `/api/*` or `/mcp`: they are not cached by default. Verify: a second
   `curl -sI https://<label>-app.<parent>/assets/<file>` shows `cf-cache-status: HIT`; `/api/whoami`
   shows `DYNAMIC`.
5. **Firewall at the VPS provider (hPanel):** inbound TCP 80, 443 and UDP 443 only from
   Cloudflare's ranges (`ops/cloudflare-ips.sh` prints them; re-run it monthly and diff); SSH
   (22) only from the NetBird range / NetBird peers, with NetBird's own UDP port open; drop
   everything else. Verify: from a non-Cloudflare host, `curl --connect-timeout 5 https://<vps ip>`
   times out; `ssh <vps>` over NetBird works and over the public address times out.

## Monitoring

`uptime-kuma` runs in the compose project, bound to `127.0.0.1:3001` on the VPS. Its admin UI is
reached with `ssh -L 3001:127.0.0.1:3001 <vps>` over NetBird and is not published: it is an
admin app with its own login, and keeping it off the internet costs nothing. Kuma on the VPS
cannot report the VPS itself being down; pair it with an external free checker (e.g. UptimeRobot).

### Public status page (`<label>-status.<parent>`)

Caddy serves `<label>-status.<parent>` (`SHIMPAKU_STATUS_HOSTS`, written by `deploy.sh` as `status.` + each
domain) by proxying **only** Kuma's public status-page paths, GET/HEAD only: `/status`,
`/status/<slug>` (and its `/rss`), `/api/status-page/<slug>…` and `/api/status-page/heartbeat/<slug>`
(the routes in Kuma's `server/routers/status-page-router.js`), plus the SPA's static files
(`/assets/*`, `/upload/*` for a logo, icons, `/manifest.json`). `/` redirects to
`/status/$SHIMPAKU_STATUS_SLUG` (default `shimpaku`). Everything else is a 404 at the edge, including
the admin routes (`/dashboard`, `/settings`, `/manage-status-page`, `/add`), `/socket.io` (the admin
API the admin UI runs on), `/api/push/*` and `/metrics`. The status page reads over plain HTTP; it
does not need socket.io.

Create it once, over the SSH tunnel to the admin UI:

1. Create the monitors below (only these are public; name them for visitors, e.g. "Dashboard",
   "Live lab"; never put an internal address in a monitor's *name* or *description*).
2. *Status Pages → New Status Page*: name "Shimpaku", slug `shimpaku` (or set
   `SHIMPAKU_STATUS_SLUG` in `.env` to match). Add the monitors you want shown, *Save*.
3. In the page settings, leave *Show Certificate Expiry* and *Show Tags* off and turn off
   "Show Powered By" if wanted; Kuma shows monitor names and uptime only, not URLs, unless
   *Show URL* is ticked on a monitor — leave it off for the lab and replay monitors.
4. DNS: a proxied `status` record for each domain, like `app`.
5. Check from outside: `https://<label>-status.<parent>/` lands on the page;
   `https://<label>-status.<parent>/dashboard` and `/socket.io/` are 404.

The landing page links to it ("Uptime status") and to the dashboard's `/architecture` page.

### Landing status strip (`GET /api/public/summary`)

The landing page (`site/index.html`, plus `site/live.js`; no inline script) fetches
`https://<label>-app.<parent>/api/public/summary` on load and shows: lab online/offline (offline = the edge is
serving the replay), devices up/total (live only; the replay says "—" rather than guess), the last
action (relative time and status), unsafe actions executed (actions that committed to a device
without an `allowed` guardrail decision, over the whole action record) and whether the audit chain
verifies. On any failure the static fallback text stays.

The endpoint needs no sign-in, returns aggregates only (no addresses, config, tokens or user
names), is cached 20 s and rate limited per address. CORS is an allow-list:
`SHIMPAKU_PUBLIC_CORS_ORIGINS="https://<domain>,https://<domain2>"`, GET only, never with
credentials. `deploy.sh` sets it for the replay container; **set the same variable on the lab
daemon** (its environment file) or the live answer will carry no CORS header and the landing will
show the fallback while the lab is up.

Monitors to create (20–60 s interval):

| Monitor | Type | Target | Expect |
|---|---|---|---|
| Landing | HTTP(s) | `https://<domain>/` | 200 |
| App shell | HTTP(s) | `https://<label>-app.<parent>/` | 200 |
| Live API via edge | HTTP(s) – keyword (JSON query) | `https://<label>-app.<parent>/api/whoami` | 200 and `replay` is `false` (alerts when serving the replay) |
| Replay | HTTP(s) | `http://replay:8780/api/whoami` | 200 |
| Lab over NetBird | HTTP(s) | `http://<lab netbird ip>:8780/api/whoami` | 200 (needs the container to reach NetBird, as for Caddy) |
| Status page itself | HTTP(s) | `https://<label>-status.<parent>/status/shimpaku` | 200 |
| TLS expiry | HTTP(s) with certificate expiry notification | `https://<label>-app.<parent>/` | alert at 14 days (the Cloudflare edge certificate; check the origin's with `curl --resolve` from the VPS) |

## First deploy

On the VPS (once): Docker with the compose plugin; NetBird joined; the firewall and Cloudflare
steps above.

From the Mac:

```bash
export SHIMPAKU_LAB_HOST=<lab host ssh name>
export SHIMPAKU_VPS_HOST=<vps ssh name>
export SHIMPAKU_DOMAIN=<domain>
export SHIMPAKU_LAB_UPSTREAM=<lab netbird ip>:8780
export SHIMPAKU_REPO_URL=https://github.com/<you>/shimpaku
deploy/vps/deploy.sh                 # snapshot → render landing → ship → compose up → check
deploy/vps/deploy.sh --no-snapshot   # ship code changes, keep the last snapshot
```

The final checks: landing 200, app shell and `/start` 200, `/api/whoami` is live or a read-only
replay; when live, `/mcp` is 401 without a token and an anonymous write is refused; when the lab
is down, `/mcp` and `POST /api/actions/x/approve` are 503 with the JSON detail.

CI (`edge` job) builds the edge image, runs `caddy validate` inside it, checks both plugins are
compiled in, and exercises failover with an unreachable lab upstream.

## Refreshing the data

Run `deploy/vps/deploy.sh` again after a demo session on the lab; the dashboard's badge shows
when the snapshot was recorded.
