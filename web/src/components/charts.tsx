import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { time } from "../format";

const tip = ({ active, payload, label, fmt }: any) =>
  active && payload?.length ? (
    <div className="tooltip">
      <div style={{ opacity: 0.75 }}>{time(label)}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey}>
          {p.name}: <b>{fmt ? fmt(p.value, p.payload) : p.value}</b>
        </div>
      ))}
    </div>
  ) : null;

/** SLE success over time: one series, goal as a dashed reference line. */
export function SleChart({ series, goal, height = 64, label }: { series: { t: number; value: number }[]; goal: number; height?: number; label: string }) {
  const min = Math.max(0, Math.min(goal - 5, ...series.map((s) => s.value)) - 5);
  return (
    <div role="img" aria-label={label} style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="gsle" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="#01A982" stopOpacity={0.35} />
              <stop offset="1" stopColor="#01A982" stopOpacity={0.04} />
            </linearGradient>
          </defs>
          <XAxis dataKey="t" hide />
          <YAxis domain={[min, 100]} hide />
          <ReferenceLine y={goal} stroke="#8A9590" strokeDasharray="3 3" />
          <Tooltip content={tip as any} formatter={undefined} cursor={{ stroke: "#56645E", strokeWidth: 1 }} />
          <Area type="monotone" dataKey="value" name="Success" stroke="#01A982" strokeWidth={2} fill="url(#gsle)" isAnimationActive={false} unit="%" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Failing vs total probe-minutes per minute (stacked; failures on the baseline). */
export function FailBars({ data, height = 160 }: { data: { t: number; fail: number; total: number }[]; height?: number }) {
  const rows = data.map((d) => ({ t: d.t, fail: d.fail, ok: Math.max(0, d.total - d.fail) }));
  return (
    <div role="img" aria-label="Failing and passing minutes over time" style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -18 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="#EEF1F0" />
          <XAxis dataKey="t" tickFormatter={time} tick={{ fontSize: 11, fill: "#56645E" }} minTickGap={40} axisLine={{ stroke: "#DCE2DF" }} tickLine={false} />
          <YAxis tick={{ fontSize: 11, fill: "#56645E" }} axisLine={false} tickLine={false} allowDecimals={false} />
          <Tooltip content={tip as any} cursor={{ fill: "rgba(1,169,130,.08)" }} />
          <Bar dataKey="fail" name="Failing" stackId="a" fill="#D93A30" isAnimationActive={false} />
          <Bar dataKey="ok" name="Passing" stackId="a" fill="#B9DCCF" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
