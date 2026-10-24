# Public deployment and guest testers — design

Status: **draft for the owner's decisions** (marked ⚑). Sources:
- `docs/research/2026-10-24-guest-access.md`
- `docs/research/2026-10-24-edge-performance.md`
- `docs/research/2026-10-24-tester-role.md`

## Goal

1. A recruiter anywhere opens a link and sees the system in seconds.
2. A person the owner invites (typically met on LinkedIn) can drive the **live** lab for a short
   session:
   - break the demo VLAN;
   - watch Shim, or their own Claude Code over MCP, propose the fix;
   - approve it;
   - watch it validate, or recur.
3. Nothing a visitor or tester does can change anything outside the demo fixture, and every step is
   attributed in the audit chain.

## Shape

```
visitor ──► Cloudflare (free) ──► edge VPS (Caddy)           ──┬─► static landing + SPA assets (local)
                                                               ├─► replay API (local container)
                                                               └─► NetBird ─► lab host: shimpakud (live)
```

**Static files and the replay stay on the VPS.** They never depend on the lab host, which is
intermittent. So the site is up even when the lab is off.

**Live API calls go over NetBird to `shimpakud` on the lab host.** If the lab host is down, Caddy's health check
fails and `/api` falls back to the replay. The dashboard badge then says "Replay · lab offline".

**Cloudflare in front gives three things:**
- a global cache for the static files;
- absorption of attacks (Hostinger null-routes the IP under a severe one);
- free HTTP/3 and Brotli.

The price is that the domain's nameservers must move to Cloudflare, because the free plan has no
partial CNAME setup.

**Server-sent events must keep heartbeats well inside Cloudflare's 125 s timeout.** The app already
pings every 15 s.

## Access levels

| Who | How they get in | Can | Cannot |
|---|---|---|---|
| Visitor | nothing | read the replay, or live reads of `sw1` and `leaf2` when the lab is up | write anything; MCP; Ask Shim |
| Tester | a single-use invite link from the owner, redeemed by tapping "Start demo" with a consent box | for one 30-minute lease: break or seed the fixture, propose/approve/reject/cancel **fixture actions from their own lease**, Ask Shim within a budget, one expiring MCP token (scope `propose`, fixture only) | any other kind, device, port or VLAN; tokens; settings; another tester's action |
| Operator | local sign-in: argon2id password + TOTP code (required with public view; one-time recovery codes as backup); 30 min idle / 4 h sessions, browser-session cookie | everything, as today | — |

**The fixture** is `add_vlan_to_trunk` on `sw1 ge-0/0/5`, VLAN 999 only. Break and seed run
through the engine, take the device lock, and are audited. Today they are CLI-only and bypass the
audit; this changes.

**One tester at a time.** Others queue. When a lease ends:
- its open actions are cancelled;
- its MCP token and LLM key are revoked;
- the fixture is re-seeded.

**Tester actions validate for 180 s, not 1800 s.** Junos's own rollback measured 73–76 s, so a
demo fits in a session.

## The gaps this must close (from the tester-role review)

1. **Any session can approve any action.** Today `can_approve` is just "has a session". Approval
   must check the role, the action's lease and that it is the fixture.
2. **`POST /api/actions` accepts every catalogue kind.** The write scope also includes test ports on
   `leaf2` and `spine2`. The fix is a fixture-only gate in `Engine.propose` for testers, enforced
   below the API so MCP gets it too.
3. **`rerun_deploy` has no device**, so it skips G0 and G5. It is refused for testers, and the G0
   gap is fixed for everyone.
4. **Tokens never expire.** They get `expires_at`, and testers get exactly one.
5. **The only rate limit is on sign-in.** Add per-principal and global limits in the app, and
   per-IP limits at the edge (`caddy-ratelimit`, built with xcaddy).
6. **Shim has no spending limit.** Each lease gets a LiteLLM key with `max_budget`, `rpm_limit`
   and `duration` matching the lease.
7. **`/mcp` answers auth failures as JSON-RPC errors.** It must return HTTP 401 with
   `WWW-Authenticate`, per the MCP 2025-06-18 authorization profile. Claude Code sends a static
   `Authorization: Bearer` header, so a per-lease bearer token is enough for now.
8. **REST approvals may not reach the hash-chained audit.** Verify this, and fix it if so.

## Identity and personal data

**Primary: invite links.**
- The token is 256-bit random, and only its SHA-256 hash is stored.
- It must be redeemed within 72 h, and is used once, on tap. That way link previews in LinkedIn or
  WhatsApp don't use it up.
- It is revocable.

**Optional: Sign in with LinkedIn** (scopes `openid profile`).
- LinkedIn says this does **not** verify identity.
- `email` may be missing from the response.
- Its `sub` is unique to each app.
- Keep only `sub` and the name, and drop the access token straight away.

**India's DPDP Act** probably applies: a portfolio used to find work is unlikely to count as
"personal or domestic purpose". So:
- a notice and a consent checkbox on the Start page;
- the purpose stated;
- data kept for 90 days, then deleted;
- deletion on request.

## Speed

**Split the 890 KB bundle by route.** Use `React.lazy` with Vite's automatic dynamic-import
chunks; `manualChunks` is outdated under Rolldown. Pre-compress assets (br, zstd, gzip), and serve
hashed assets with `Cache-Control: immutable`.

**Targets** are Core Web Vitals at p75: LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1. They are checked from a
browser in CI against the replay image.

**Monitoring:** Uptime Kuma on the VPS, checking the landing page, the replay, live `/api/whoami`
and the TLS expiry, with a public status page.

## Decisions for the owner

- ⚑ **D1 — Domain.** Which domain or subdomain?
- ⚑ **D2 — Cloudflare free in front.** It needs the domain's nameservers. (Recommended.)
- ⚑ **D3 — LinkedIn sign-in in addition to invite links**, or invite links only. (Recommended:
  invite links only first; LinkedIn later if wanted.)
- ✔ **D4 — Tester reads** (built with the recommended default, 2026-10-24). Live reads of the whole lab, or `sw1`/`leaf2` only. (Recommended:
  everything read-only except raw `show` and config reads, which stay limited to the fixture devices.)
- ⚑ **D5 — Shim budget per lease** in USD. (Suggested: $0.50.)

## Build order

**Safety first; the edge last.** Each item is a small slice with its own tests.

1. **Roles.** Add `role` and `lease_id` to principals, callers and audit rows. Audit REST approvals.
2. **Tester gate in the engine.** Fixture only; approve, reject and cancel only for their own lease;
   no `rerun_deploy`; fix G0 for actions with no device.
3. **Break and seed as audited service actions.** Add the per-action validation window (180 s for
   testers).
4. **Invites, leases and queue.** Expiring tokens; cleanup at lease end; the Start page with the
   notice.
5. **Limits.** Rate limits; a LiteLLM key per lease; HTTP 401 on `/mcp`.
6. **Red team.** Re-run against tester sessions and tokens.
7. **Speed.** Bundle split and precompression.
8. **Edge.** Caddy with the rate-limit module, failover to the replay, Cloudflare IP allow-list,
   and Uptime Kuma.
