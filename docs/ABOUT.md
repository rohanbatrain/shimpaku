# About Shimpaku

## Who built it

Shimpaku, and Shim, its built-in assistant, were designed and built in 2026 by two engineers:
**Aryan** and **Rohan**. There is no company behind it; it is an independent project.

## Where the code is

Everything (source code, design notes, the incident log, the benchmark) is on GitHub:
<https://github.com/rohanbatrain/shimpaku>.

## What Shim runs on

Shim calls its language models through the operator's own LiteLLM gateway. Which models it uses
is set in the daemon's configuration (`SHIMPAKU_LLM_MODELS`, one per role: triage, diagnose,
escalate); by default those are NVIDIA Nemotron models. Shim does not run on any one vendor's
model by design: the guardrails, the audit chain and human approval are what keep it safe, not the
model.

## Names in the build notes

Labels such as `B-assistant`, `B-web`, `B-telemetry`, `B-mcp`, `B-analytics` and `B-bench` in the
documents name the build workstreams the project was split into, not people.

## Not affiliated

Shimpaku is an independent learning project. It is not affiliated with, endorsed by, or built
from materials of Hewlett Packard Enterprise or Juniper Networks beyond their public documentation.
All trademarks belong to their owners.
