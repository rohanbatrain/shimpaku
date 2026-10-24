import { lazy, Suspense, type ComponentProps } from "react";
import type { SleChart as SleChartImpl } from "./charts";

// recharts is the single largest dependency (~360 kB raw). Pages keep their text and layout
// eager and fetch the chart code after first paint; the placeholder keeps the same height (no CLS).
const Impl = lazy(() => import("./charts").then((m) => ({ default: m.SleChart })));

export function SleChart(props: ComponentProps<typeof SleChartImpl>) {
  return (
    <Suspense fallback={<div role="img" aria-label={props.label} style={{ width: "100%", height: props.height ?? 64 }} />}>
      <Impl {...props} />
    </Suspense>
  );
}
