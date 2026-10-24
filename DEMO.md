# Showing Shimpaku — a 10-minute walkthrough

Use this when you're live (interview, screen share, recorded video). The README is the
document to send beforehand or leave behind; this is the script for the room.

## Before you start (2 minutes, do this off-camera)

```bash
ssh -N -L 8780:127.0.0.1:8780 <lab-host> &     # tunnel to the lab host
open http://127.0.0.1:8780                 # the dashboard
```

Check the service is actually up, so you're not debugging live:

```bash
ssh <lab-host> 'systemctl --user is-active shimpakud.service'   # should print: active
```

If it's not active: `ssh <lab-host> 'systemctl --user restart shimpakud.service'`, wait ~15s.

## The one-line pitch (say this first)

> "This is a Mist-style assurance system I built for my own Junos home lab — not a mockup, the
> real HPE Juniper Mist AI shape: service levels, blast-radius isolation, guardrailed actions,
> an MCP server any agent can drive. Everything I'm about to show you is running against real
> vJunos switches right now, not a recording."

## The walkthrough

### 1. Service Levels (30s)
Open the dashboard, land on **Service levels**. Point at:
- the **n = …** next to every percentage — say: *"these are probe-minutes, not real users —
  I say that everywhere, I don't borrow Mist's language for something I haven't built."*
- the **Changes** timeline at the top — commits, faults and actions, so a viewer can correlate
  a dip with what caused it.

### 2. Break something, live (2 minutes — the centerpiece)

In a second terminal:

```bash
ssh <lab-host>
cd ~/src/shimpaku
.venv/bin/python inject_fault.py        # removes VLAN 999 from sw1's trunk — a real Junos commit
```

Say: *"That just ran a real `commit` on the switch. No mock."* Then propose the fix the way an
agent would — over MCP, the same protocol Claude Code or OpenCode would use:

```bash
.venv/bin/python mcp_client.py action_propose_add_vlan_to_trunk \
  '{"device":"sw1","ifname":"ge-0/0/5","vlan":999,"reason":"demo: VLAN missing from trunk"}'
```

Point at the output: `"status": "awaiting_approval"`. Say: *"The agent can propose, it cannot
execute. An LLM's 'yes' isn't a human's — that's enforced in code, not in the prompt."*

Switch to the dashboard's **Actions** tab — the proposal is sitting there. Click **Approve**.
Watch it move: `running` → two real `commit confirmed` legs → `validating`. Flip back to
**Service levels** or **Root cause** and point out the VLAN is back on the switch.

> If you want to skip live risk, the same sequence is already proven and recorded in the
> README's evidence table and `docs/build/mcp-actions.md` — you can walk through that instead
> and say "I ran this live earlier today," which is also true.

### 3. Ask Shim (1 minute)

Press `⌘K` / `Ctrl+K`. Ask something concrete tied to what you just did:

> "Why did sw1's trunk need a VLAN added back?"

Point at the tool-call trace it shows under the answer — *"it's reading the same MCP tools,
not guessing."* (Needs the LiteLLM key configured — see `~/.config/shimpaku/litellm.key` — if
it's not set up, say so plainly and show the eval results in `docs/build/assistant.md` instead.)

### 4. The audit log (30s)

Dashboard → wherever `/api/audit` is surfaced (Actions → the action you just ran → its audit
rows), or:

```bash
curl -s http://127.0.0.1:8780/api/audit | python3 -m json.tool | head -20
```

Say: *"Every tool call is hash-chained — if someone edits a row after the fact, the chain
breaks and it's detectable. I tested that: it catches an edited row."*

### 5. The benchmark (1 minute)

Dashboard → **Benchmark**, or:

```bash
curl -s http://127.0.0.1:8780/api/bench/results | python3 -m json.tool | head -30
```

Say: *"This is the part most AI-networking demos skip. Sixteen fault scenarios, five repeats
each, eighty of eighty trials passed — and every trial is independently verified, not scored
by the agent grading its own homework. Ground truth lives in a database the system under test
can never open."*

### 6. Close (30s)

> "What makes this different from HPE's own Mist MCP server — which exists, I checked — is
> that theirs is read-only by design; writes go through our own layer, with the guardrails,
> the lock, and the validation window. And the honest half of this project is the README's
> table of what I did *not* build and why — Wi-Fi capacity SLEs, an LSTM, anything that would
> need hardware or data I don't have."

## If something breaks mid-demo

- **Service down**: `ssh <lab-host> 'systemctl --user restart shimpakud.service'`, wait 15s, reload.
- **The VLAN fault won't re-inject** (already faulted): check first —
  `ssh <lab-host> 'source /tmp/jx.sh; j $SW1 "show vlans" | grep -A1 shimpaku-demo-999'`. If the
  interface list is already empty, skip straight to the propose step.
- **Dashboard shows stale "running" on the demo action**: it auto-validates for 30 minutes;
  that's expected, not broken — say so.
- **No LiteLLM key configured**: skip step 3, say "the gateway isn't wired in for this
  session" and show `docs/build/assistant.md`'s eval numbers instead — they're real, just not
  live.

## Likely questions, short answers

- **"Is this connected to real Mist?"** No — Juniper's terms forbid benchmarking Mist or
  copying its UI from an account, so nothing here ever touched one.
- **"Why not Kafka / an LSTM / Kubernetes?"** Seven devices don't need a message bus; a simple
  rate test beat an LSTM on simulated data with fewer false alarms — both are in the README's
  honesty table with the reasoning.
- **"What stops the agent from doing something dangerous?"** Nine guardrails run as plain
  Python over live facts before any device is touched — not a prompt instruction. Show
  `docs/build/mcp-actions.md`'s red-team table: 0 unsafe actions executed across a dozen
  attack cases.
