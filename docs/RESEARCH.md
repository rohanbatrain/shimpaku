# Research behind the design

Code comments cite research like `R7 §4.1` or `REVIEW B2`. The full reports are private working
notes (they record lab-host details); this page says what each one established, so every
citation stays traceable. Every external fact in them was checked against the primary source
(Juniper/HPE documentation, library docs, papers), with the page saved at the time.

| ID | Question | What it established, in one line |
|---|---|---|
| **R1** | What does Mist AI actually do? | 106 claims checked against Juniper/HPE docs only. Confirmed the SLE/user-minute model, mutual information for scope, Marvis Actions guardrails (port bounce ≤ 3, trunk protection, multi-AP correlation) and validation windows. Corrected several common misstatements (wired and WAN have four SLEs each; five action statuses, not six). Found Juniper's trademark guidance against imitating its sites' look |
| **R2** | What telemetry works on vJunos 26.2? | gNMI works in clear text via `system services http servers … grpc gnmi`; `/junos/events` streams every syslog event with structure in ~0.9 s; RPM probes give real loss/RTT/jitter; NETCONF reads cost 120–330 ms (pool the sessions) |
| **R3** | Can clients be emulated without Wi-Fi hardware? | Yes for connection journeys: `mac80211_hwsim` + hostapd + FreeRADIUS + DHCP/DNS, 24 clients timed stage by stage, 16 faults verified. No for RF capacity: virtual radios report no retries or channel load |
| **R4** | What already exists in open source? | Juniper ships `junos-mcp-server` (CLI passthrough, plain `commit`) and HPE hosts a read-only Mist MCP server; NIKA is a network-troubleshooting benchmark. A message bus is not justified at seven devices |
| **R5** | Which analytics hold up? | The naive "max mutual information, then max lift" scope rule fails (0–20 % top-1, false scopes on no-fault data); a significance-gated rule reaches 95–100 %. A rate test and a seasonal profile beat an LSTM on false alarms (simulated data). Defines the scoring contract |
| **R6** | What would an HPE/Juniper reviewer value? | Live postings ask for approval gates, blast-radius limits, rollback, MCP tool servers, fault corpora and replayable harnesses. A chatbot over SSH is the anti-pattern |
| **R7** | How should the agent layer work? | One MCP server as the only path to devices; the model only proposes; guardrails in code; Junos allows one pending `commit confirmed` per box, so writers need a lock |
| **R8** | What should the product look like? | Copy Mist's information architecture, never its look; Grommet (Apache-2.0) with an original theme; feature-flagged auth |
| **R9** | Can faults be injected and verified independently? | 35 scenarios catalogued; 10 self-tested live (inject → verify → revert → verify); a second `commit confirmed` before the first is confirmed rolls back only one step, leaving the fault in place |
| **REVIEW** | Red-team of the plan as a sceptical Mist engineer | Two blockers fixed before build: the remediation benchmark measured a Junos timer, and ground truth was readable by agents. Also: call the unit a probe-minute and show `n` |
| **PLAN** | Feasibility plan built from R1–R9 + REVIEW | The MVP phases, exit criteria, and the "what we will not claim" list in the README |
