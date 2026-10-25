// Once the first page is up and the browser is idle, fetch every page's code and warm the GET cache
// with what each page asks for first, so moving around during a demo never waits on the network.
// Live data still refreshes on each page's own poll; this only removes the first, empty wait.
import { prefetch } from "./api";

const PAGES = [
  () => import("./pages/Overview"), () => import("./pages/Topology"), () => import("./pages/Hardware"),
  () => import("./pages/Device"), () => import("./pages/Events"), () => import("./pages/ServiceLevels"),
  () => import("./pages/RootCause"), () => import("./pages/Actions"), () => import("./pages/ActionDetail"),
  () => import("./pages/Theatre"), () => import("./pages/Guardrails"), () => import("./pages/Audit"),
  () => import("./pages/AssistantTrace"), () => import("./pages/Benchmark"), () => import("./pages/Architecture"), () => import("./pages/DeployOwn"),
  () => import("./pages/Probes"), () => import("./components/AssistantPanel"), () => import("./pages/Shim"), () => import("./components/Topo3D"), () => import("./components/ShimMarkdown"),
];

const DEVICES = ["r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"];
const DATA = [
  "/api/viz/overview", "/api/viz/topology", "/api/hardware", "/api/actions", "/api/incidents", "/api/live/range",
  "/api/sle?site=campus&window=60", "/api/probes/heatmap?site=campus&window=60",
  "/api/explain/funnel?window=7d", "/api/explain/funnel?caller=all&window=7d", "/api/explain/audit/chain?limit=15",
  "/api/bench/results", "/api/theatre/runs",
  ...DEVICES.map((d) => `/api/viz/devices/${d}?window=60`),
];

let started = false;
export function warmUp() {
  if (started) return;
  started = true;
  const idle = (f: () => void) => ("requestIdleCallback" in window ? window.requestIdleCallback(f, { timeout: 3000 }) : setTimeout(f, 1500));
  idle(() => {
    PAGES.forEach((p) => p().catch(() => undefined));
    prefetch(DATA);
  });
}
