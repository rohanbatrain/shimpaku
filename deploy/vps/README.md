# Public deployment: landing page + read-only replay

The public site never reaches the lab. It serves two things from one VPS:

| Host | What | Served by |
|---|---|---|
| `<domain>` | The landing page (`site/index.html`) | Caddy, static |
| `app.<domain>` | The dashboard, replaying a snapshot recorded from the live lab | `python -m shimpaku.api` with `SHIMPAKU_REPLAY` |

## Why a replay and not the live system

The live daemon can change switch configuration. Its controls (approval only from a signed-in
dashboard session, guardrails, `commit confirmed`) are designed for a trusted operator, not for
the open internet, and the lab has no reason to be reachable from it. So the public host gets a
**snapshot**: every number in it was recorded from the live system, and nothing is re-timed or
re-scored. The container holds no device credentials, has no Junos libraries, has no LLM key,
and cannot open a connection to the lab. Writes are refused (`403`), the visitor is an anonymous
reader, `/mcp` is closed at the proxy, and Ask Shim answers that it is off.

For a live walkthrough (an interview), use the SSH tunnel in `DEMO.md`.

## What a snapshot contains

`shimpaku snapshot export` (on the lab host) reads every route the dashboard reads and writes one
JSON file. On the way out it drops management addresses, rewrites home paths to `~`, replaces
each `--redact` term (the lab host's name, the service user) with `<redacted>`, and **refuses to
write** if any of those terms survives, or if the live audit chain does not verify. `deploy.sh`
then refuses to ship it if it mentions the ssh names of the lab host or the VPS.

## Container hardening

`compose.yaml`: read-only root filesystem, `tmpfs` for `/tmp`, all capabilities dropped,
`no-new-privileges`, non-root user (uid 10001), 512 MB memory and 128-process limits, no
published port (only Caddy reaches it). Caddy terminates TLS (automatic certificates), adds HSTS,
and strips its `Server` header; the app sets its own CSP and frame headers.

## First deploy

On the VPS (once): Docker with the compose plugin; ports 80 and 443 open; DNS `A`/`AAAA`
records for both `<domain>` and `app.<domain>` pointing at it.

From the Mac:

```bash
export SHIMPAKU_LAB_HOST=<lab host ssh name>
export SHIMPAKU_VPS_HOST=<vps ssh name>
export SHIMPAKU_DOMAIN=<domain>
export SHIMPAKU_REPO_URL=https://github.com/<you>/shimpaku
deploy/vps/deploy.sh                 # snapshot → render landing → ship → compose up → check
deploy/vps/deploy.sh --no-snapshot   # ship code changes, keep the last snapshot
```

The final checks assert that `app.<domain>/api/whoami` reports a read-only replay, the landing
page answers 200, and `/mcp` answers 404.

## Refreshing the data

Run `deploy/vps/deploy.sh` again after a demo session on the lab; the dashboard's badge shows
when the snapshot was recorded.
