# Going live

One script, one settings file. Every step is idempotent, and `check` changes nothing.

```bash
ops/golive/run.sh check        # creates .local/settings.env on first run; fill it in
ops/golive/run.sh vps-key      # one-time: install your SSH agent's key on the VPS (asks the root password)
ops/golive/run.sh lab-secrets  # on the lab host: operator password + TOTP (secret and recovery codes saved to .local/)
ops/golive/run.sh lab-config   # on the lab host: sign-in on, public read-only view, NetBird bind, trusted edge, Virga live drills
ops/golive/run.sh deploy       # snapshot → landing → edge on the VPS → checks
ops/golive/run.sh kuma         # Uptime Kuma: monitors and the public status page (groups Website and Live lab)
ops/golive/run.sh verify       # public checks only
```

`.local/` is git-ignored and mode 700. It holds:
- `settings.env`, your values;
- `golive.log`, every step with a timestamp;
- `totp-*.txt`, the TOTP secret and recovery codes. Move those into Bitwarden, then delete the file;
- `kuma-admin.txt`, which you create (mode 600): line 1 the Uptime Kuma admin user, line 2 its password.
  `kuma` reads it into one process's environment; nothing prints it. A fresh Kuma gets this admin created.

**Not in this script:** the VPS baseline (users, firewall, NetBird, Docker, memory slices). That is
Ansible in `infra-substrate` (ADR-0056), and `check` tells you when it is missing.

You type the NetBird setup key on the VPS yourself (RUN-0004).

Before the first `deploy`, do the Cloudflare steps in `deploy/vps/README.md`: move the domain's
nameservers to Cloudflare, set up DNS and TLS mode, and allow only Cloudflare in hPanel.

## Live fault drills (Virga live)

Design: `docs/build/virga-live.md`. The operator injects a real fault from the dashboard's **Fault lab**;
the landing page's live strip, the dashboard and the public status page show it, then Virga reverts it.

1. **Enable it on the lab.** `ops/golive/run.sh lab-config` now also writes `SHIMPAKU_VIRGA_LIVE=1` into the
   lab's env file and restarts `shimpakud`. `check` reports whether it is on. Without it the Fault lab is
   read-only and says injects are disabled. Run the lab-config after the lab host has the Virga live build
   (merge, then `git pull` on the lab host), or the daemon ignores the setting.
2. **Ship the edge and landing page.** `ops/golive/run.sh deploy`. Caddy gets the `virga` rate-limit zone
   (10 injects, reverts or recovers per 10 min per client IP) and the landing strip learns the drill banner.
   The deploy prints `api/public/health: campus=… fabric=… drill=… lab=…`, or a note if the lab daemon does
   not serve it yet.
3. **Create the Live lab monitors.** `ops/golive/run.sh kuma` (needs `.local/kuma-admin.txt`, and this
   machine on NetBird, because Kuma's admin UI answers on the VPS's NetBird address). It creates the groups
   "Website" and "Live lab" and republishes the status page. Re-run it any time; it only changes what differs.
   Details, and why no alert notifications are attached: `deploy/vps/README.md`, "Virga live".
4. **Check.** `ops/golive/run.sh verify` prints the health answer and that an anonymous
   `POST /api/virga/trials` is refused (401 or 403). On the status page the three Live lab monitors are green.
5. **Rehearse once** with `DEMO.md` ("A live fault drill"): the monitors should turn red within a minute of
   the inject and green after the revert is verified.

Who can inject: only the operator's own dashboard session (password and TOTP), never a token, a tester or a
visitor. One trial runs at a time; a drill is a `commit confirmed` with a rollback timer, so a daemon that
dies mid-drill is rolled back by the device itself, and `virga recover` (or **Recover** in the Fault lab)
cleans up from the journal.
