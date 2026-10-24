# Showing Shimpaku — a 10-minute walkthrough

Use this when you're live (interview, screen share, recorded video). The README is the
document to send beforehand; this is the script for the room.

## Before you start (off camera, 2 minutes)

```bash
export SHIMPAKU_LAB_HOST=<lab-host>                     # the lab host's ssh name
ssh -N -L 8780:127.0.0.1:8780 "$SHIMPAKU_LAB_HOST" &      # tunnel to the dashboard
open http://127.0.0.1:8780
ssh "$SHIMPAKU_LAB_HOST" 'systemctl --user is-active shimpakud'   # → active
```

Open a second terminal on the lab host for the live part, with the service's settings loaded:

```bash
ssh "$SHIMPAKU_LAB_HOST"
cd ~/src/shimpaku && set -a && . ~/.config/shimpaku/env && set +a
alias shimpaku=.venv/bin/shimpaku
shimpaku demo seed      # idempotent; makes sure the demo trunk exists
shimpaku demo status    # → VLAN 999 on sw1 ge-0/0/5: present
```

## The pitch (say this first)

> "This is a Mist-style assurance system I built for my own Junos lab. Same shape as HPE
> Juniper's Mist AI: service levels, blast-radius isolation, guardrailed actions, and an MCP
> server any agent can drive. Everything you'll see runs against real vJunos switches."

## The walkthrough

### 1. Service levels (30 s)
Point at the **n = …** beside every percentage: *"These are probe-minutes from synthetic
probes, not users. I don't borrow Mist's terms for things I haven't built."* Then the
**Changes** timeline: commits, faults and actions on one axis.

### 2. Break it and fix it, live (3 minutes — the centrepiece)

```bash
shimpaku demo break     # a real Junos commit: VLAN 999 removed from the sw1 trunk
shimpaku demo status    # → MISSING
shimpaku demo propose   # what an agent does over MCP → "status": "awaiting_approval"
```

Say: *"The agent proposed the fix. It cannot execute it — approval isn't even an MCP tool.
Before it was allowed to propose, nine guardrails ran in Python: it's a trunk port, it's in
scope, and VLAN 999 already exists on a second switch, so there's evidence it belongs."*

Dashboard → **Actions** → the new action → **Approve**. Watch `running` → `validating`. Then:

```bash
shimpaku demo status    # → present
```

Say: *"Two real commits: the first as `commit confirmed`, confirmed only after a fresh health
check. If anything had died mid-change, Junos would have rolled it back by itself."*

**Optional, strongest moment:** run `shimpaku demo break` again while it's validating. Within
a minute the action turns **recurred**. *"It doesn't trust its own fix. It watches the symptom
for the whole window, and when the symptom comes back it reopens the issue."*

### 3. Ask Shim (1 minute)
`⌘K` / `Ctrl+K`: *"Why did sw1's trunk need a VLAN added back?"* Point at the tool trace under
the answer. (Needs the LLM gateway key in `~/.config/shimpaku/env`. Without it, say so and show
`docs/build/assistant.md`.)

### 4. Audit (30 s)
```bash
curl -s "127.0.0.1:8780/api/audit?action_id=<the action id>" | python3 -m json.tool | head -30
curl -s 127.0.0.1:8780/api/audit/verify          # → {"ok": true, "rows": N}
```
*"Every tool call is a hash-chained row: who called what, the policy decision, the commit it
produced. The verifier recomputes the chain; editing any row breaks it, and that's tested."*

### 5. Benchmark (1 minute)
Dashboard → **Benchmark**. *"Sixteen fault scenarios, five repeats each, 80 of 80 trials. Each
fault is verified by an independent probe, never by the system grading itself, and ground
truth sits in a store the system can't read."*

### 6. Honesty (1 minute — this lands well)
Open `docs/INCIDENTS.md`. *"These are eight defects I found by running it. Number eight: my own
system once said 'verified' when the fault was back. I didn't edit the record. I fixed the
validation, added a test that fails on the old code, and wrote it up."*

### 7. Close (30 s)
> "HPE's own Mist MCP server exists, and it's read-only by design. The interesting part is the
> write path: proposal, guardrails, human approval, lock, `commit confirmed`, validation. And
> the README lists what I deliberately didn't build, and why."

## Session requests

With `SHIMPAKU_PUBLIC_VIEW=1` (and `SHIMPAKU_AUTH_MODE=local`) anyone can open the dashboard
read-only with no sign-in: live service levels, root cause, actions, audit; no writes, no tokens,
no `/mcp`, no Ask Shim. The top bar shows **Viewing read-only** and **Request a live session**.

1. A visitor fills in `/request`: name, e-mail or LinkedIn URL, a message (≤ 500 chars) and up to
   three start times (1 h to 30 days ahead), and ticks the notice. They get a status page
   (`/request/status?s=…`); that link is their only key, so they should keep it.
2. You see it under **Admin → Session requests** (nav badge = pending count), or on the host:

```bash
shimpaku admin requests list                                   # pending first, with name/contact/message
shimpaku admin requests approve req_… --choice 2                # one of their slots, or:
shimpaku admin requests approve req_… --slot 2026-11-02T15:00+05:30
shimpaku admin requests decline req_… --note "Fully booked this month"
```

3. Approving creates a single-use invite bound to a 30-minute slot. The link works from 10 min
   before the start until the end; the lease ends at the earlier of 30 min after they start and
   5 min after the slot. You get the link (copy it) and an `.ics`; their status page shows the same
   link and an **Add to calendar** file. Nothing is e-mailed automatically: send the link yourself.
4. Unanswered requests expire when their times pass; unused approvals expire at the slot end.
   Name, contact and message are deleted 90 days after the slot or the decline.

## Ad-hoc tester passes

A tester drives the live lab alone for 30 minutes: break VLAN 999, propose (or let their own
Claude Code propose) the fix, approve it, watch it validate. Needs `SHIMPAKU_AUTH_MODE=local` and
`SHIMPAKU_PUBLIC_URL` (the https base the link is built from) in `~/.config/shimpaku/env`.

```bash
shimpaku admin invite create --note "Jane Doe, recruiter at X"   # prints the link once
shimpaku admin invite list                                        # open / redeemed / expired / revoked
shimpaku admin invite revoke inv_…                                # also ends their lease within seconds
```

- Send the link. Opening it (or a LinkedIn/WhatsApp preview) does not use it up; it is used when
  they tick the consent box and press **Start demo**. It must be used within 72 h.
- If someone else holds the lab, they queue; the banner shows their position and promotes them.
- On **Actions** they get Break VLAN 999 / Propose the fix / Re-seed, and **Connect your own agent**,
  which shows their MCP token once as a `claude mcp add --transport http … --header
  "Authorization: Bearer …"` command. It proposes only the fixture and dies with the lease.
- They may approve only their own fixture action; it validates for 180 s.
- When the lease ends (30 min, **End session**, or `POST /api/admin/leases/<id>/revoke`) their open
  actions are cancelled, their token, session and LLM key are revoked, and the fixture is re-seeded.
- Ask Shim for testers needs `SHIMPAKU_LLM_ADMIN_KEY` (each lease gets its own LiteLLM key, USD 0.50);
  without it they are told the assistant is off.
- Their note and display name are deleted 90 days after the lease (daily, or `shimpaku admin purge`).
  Tunables: `SHIMPAKU_INVITE_TTL_HOURS`, `SHIMPAKU_LEASE_MINUTES`, `SHIMPAKU_LEASE_MAX_ACTIVE`,
  `SHIMPAKU_LEASE_DEMO_OPS`, `SHIMPAKU_RETENTION_DAYS`, `SHIMPAKU_PRIVACY_CONTACT`,
  `SHIMPAKU_TESTER_LLM_BUDGET_USD`, `SHIMPAKU_TESTER_LLM_RPM`, `SHIMPAKU_RATE_LIMITS`.

## Demoing from someone else's laptop

When you only have a borrowed machine (a meeting-room PC, an interviewer's laptop):

1. Open `https://app.<domain>`. Everyone sees the live lab read-only; nothing to install.
2. Click **Operator sign in** (bottom of the side menu; on a phone, in the menu).
3. Type the password, then the 6-digit code from your authenticator app. No phone? **Use a
   recovery code** (from your password manager; each works once).
4. **Never let the browser save the password**: decline "Save password?" every time; don't use
   the machine's password manager or autofill.
5. Demo as usual (break, propose, approve, audit).
6. Press **Sign out** (side menu, or top right on a phone). It clears the app's data in the
   browser and returns to the read-only view. The session would end anyway after 30 minutes idle,
   4 hours at most, or when the browser closes, but don't rely on that.
7. Walked away without signing out? From your own phone or laptop: **Settings → Sign out
   everywhere**. Think the password was seen? Change it (`shimpaku admin set-password`) on the lab host.

Set-up (once, on the lab host, before public view is on): `shimpaku admin set-password` and
`shimpaku admin totp enable`.

## If something goes wrong

| Problem | Do this |
|---|---|
| Dashboard won't load | `ssh "$SHIMPAKU_LAB_HOST" 'systemctl --user restart shimpakud'`, wait 15 s |
| `demo propose` says `blocked` with "already has an action in flight" | That's guardrail G5 (one action per device). Show it, then approve or wait out the existing one |
| `demo break` says "already broken" | Skip to `demo propose` |
| No LLM key | Skip step 3 and show the eval results in `docs/build/assistant.md` |

## Questions you'll likely get

- **"Is this connected to Mist?"** No. Juniper's cloud terms forbid benchmarking Mist or copying
  its UI from an account, so nothing here touched one.
- **"Why no Kafka or LSTM?"** Seven devices don't need a bus, and on simulated data a rate test
  beat an LSTM with fewer false alarms. Both are in the README's "not built" table.
- **"What stops the agent doing damage?"** Code, not prompts. `SECURITY.md` has the threat
  model; `docs/build/mcp-actions.md` has the red team: 0 unsafe actions executed.
