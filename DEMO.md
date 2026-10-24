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
