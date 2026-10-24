# Going live

One script, one settings file. Every step is idempotent, and `check` changes nothing.

```bash
ops/golive/run.sh check        # creates .local/settings.env on first run; fill it in
ops/golive/run.sh vps-key      # one-time: install your SSH agent's key on the VPS (asks the root password)
ops/golive/run.sh lab-secrets  # on the lab host: operator password + TOTP (secret and recovery codes saved to .local/)
ops/golive/run.sh lab-config   # on the lab host: sign-in on, public read-only view, NetBird bind, trusted edge
ops/golive/run.sh deploy       # snapshot → landing → edge on the VPS → checks
ops/golive/run.sh verify       # public checks only
```

`.local/` is git-ignored and mode 700. It holds:
- `settings.env`, your values;
- `golive.log`, every step with a timestamp;
- `totp-*.txt`, the TOTP secret and recovery codes. Move those into Bitwarden, then delete the file.

**Not in this script:** the VPS baseline (users, firewall, NetBird, Docker, memory slices). That is
Ansible in `infra-substrate` (ADR-0056), and `check` tells you when it is missing.

You type the NetBird setup key on the VPS yourself (RUN-0004).

Before the first `deploy`, do the Cloudflare steps in `deploy/vps/README.md`: move the domain's
nameservers to Cloudflare, set up DNS and TLS mode, and allow only Cloudflare in hPanel.
