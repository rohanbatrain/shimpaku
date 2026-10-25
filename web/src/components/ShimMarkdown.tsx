// How Shim's answers are drawn: GitHub-flavoured Markdown (tables, task lists, strikethrough),
// highlighted code with a copy button, ```chart blocks drawn with the dashboard's own Recharts, and
// ```mermaid diagrams. Loaded lazily, only once an answer is shown.
// Safety: no raw HTML is rendered (react-markdown's default), images are not loaded (an answer must
// not make the browser fetch a URL), Mermaid runs with securityLevel "strict", and links to other
// sites open in a new tab without a referrer.
import { Children, isValidElement, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import ini from "highlight.js/lib/languages/ini";
import plaintext from "highlight.js/lib/languages/plaintext";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const LANGS = { bash, sh: bash, shell: bash, json, yaml, python, typescript, ts: typescript, javascript: typescript, js: typescript, xml, html: xml, ini, toml: ini, junos: plaintext, text: plaintext, plaintext };
const PALETTE = ["#01A982", "#2F6690", "#D99A06", "#7D4CDB", "#D93A30", "#00739D", "#8C9894"];

function textOf(n: ReactNode): string {
  if (typeof n === "string" || typeof n === "number") return String(n);
  if (Array.isArray(n)) return n.map(textOf).join("");
  if (isValidElement(n)) return textOf((n.props as { children?: ReactNode }).children);
  return "";
}

function CopyBtn({ get, label = "Copy" }: { get: () => string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="md-copy" onClick={() => { void navigator.clipboard?.writeText(get()).then(() => { setDone(true); setTimeout(() => setDone(false), 1400); }); }}>
      {done ? "Copied" : label}
    </button>
  );
}

/* ---------------------------------------------------------------- charts */
type Series = { name: string; data: number[] };
type ChartSpec = { type?: "bar" | "line" | "area" | "pie"; title?: string; x?: (string | number)[]; series?: Series[]; unit?: string; data?: { name: string; value: number }[]; stacked?: boolean };

function ChartBlock({ src, done }: { src: string; done: boolean }) {
  const spec = useMemo<ChartSpec | null>(() => { try { return JSON.parse(src); } catch { return null; } }, [src]);
  if (!spec) return done ? <div className="md-chart-err">Shim sent a chart it could not draw.<pre className="md-code"><code>{src}</code></pre></div> : <div className="md-chart md-chart-wait">Drawing a chart…</div>;
  const unit = spec.unit ?? "";
  const fmt = (v: number) => `${Math.round(v * 100) / 100}${unit}`;
  let body: ReactNode;
  if (spec.type === "pie") {
    const data = (spec.data ?? []).filter((d) => typeof d.value === "number");
    body = (
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" outerRadius="78%" label={(d: { name?: string }) => d.name ?? ""}>
          {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
        </Pie>
        <Tooltip formatter={(v) => fmt(Number(v))} />
      </PieChart>
    );
  } else {
    const series = (spec.series ?? []).filter((s) => Array.isArray(s.data));
    const rows = (spec.x ?? series[0]?.data.map((_, i) => i + 1) ?? []).map((x, i) => Object.fromEntries([["x", x], ...series.map((s) => [s.name, s.data[i]])]));
    const common = (
      <>
        <CartesianGrid strokeDasharray="3 3" stroke="#e3ebe7" />
        <XAxis dataKey="x" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => fmt(Number(v))} width={48} />
        <Tooltip formatter={(v) => fmt(Number(v))} />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
      </>
    );
    body = spec.type === "line" ? (
      <LineChart data={rows}>{common}{series.map((s, i) => <Line key={s.name} type="monotone" dataKey={s.name} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2} dot={rows.length < 30} isAnimationActive={false} />)}</LineChart>
    ) : spec.type === "area" ? (
      <AreaChart data={rows}>{common}{series.map((s, i) => <Area key={s.name} type="monotone" dataKey={s.name} stackId={spec.stacked ? "a" : undefined} stroke={PALETTE[i % PALETTE.length]} fill={PALETTE[i % PALETTE.length]} fillOpacity={0.18} isAnimationActive={false} />)}</AreaChart>
    ) : (
      <BarChart data={rows}>{common}{series.map((s, i) => <Bar key={s.name} dataKey={s.name} stackId={spec.stacked ? "a" : undefined} fill={PALETTE[i % PALETTE.length]} radius={[3, 3, 0, 0]} isAnimationActive={false} />)}</BarChart>
    );
  }
  return (
    <figure className="md-chart" aria-label={spec.title ?? "Chart"}>
      {spec.title && <figcaption>{spec.title}</figcaption>}
      <div style={{ width: "100%", height: 220 }}><ResponsiveContainer>{body as React.ReactElement}</ResponsiveContainer></div>
    </figure>
  );
}

/* ---------------------------------------------------------------- mermaid */
let mermaidReady: Promise<typeof import("mermaid").default> | null = null;
function MermaidBlock({ src, done }: { src: string; done: boolean }) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    if (!done) return;
    let live = true;
    mermaidReady ??= import("mermaid").then((m) => { m.default.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", fontFamily: "Inter, system-ui, sans-serif" }); return m.default; });
    mermaidReady.then((m) => m.render(`m${id}`, src)).then((r) => { if (live) setSvg(r.svg); }).catch(() => { if (live) setErr(true); });
    return () => { live = false; };
  }, [src, done, id]);
  if (err) return <pre className="md-code"><code>{src}</code></pre>;
  if (!svg) return <div className="md-chart md-chart-wait">Drawing a diagram…</div>;
  return <div className="md-mermaid" role="img" aria-label="Diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}

/* ---------------------------------------------------------------- markdown */
function components(done: boolean): Components {
  return {
    pre({ children }) {
      const code = Children.toArray(children)[0];
      const cls = isValidElement(code) ? String((code.props as { className?: string }).className ?? "") : "";
      const lang = (cls.match(/language-([\w-]+)/) ?? [])[1] ?? "";
      const raw = textOf(code).replace(/\n$/, "");
      if (lang === "chart") return <ChartBlock src={raw} done={done} />;
      if (lang === "mermaid") return <MermaidBlock src={raw} done={done} />;
      return (
        <div className="md-pre">
          <div className="md-pre-h"><span>{lang || "text"}</span><CopyBtn get={() => raw} /></div>
          <pre className="md-code">{children}</pre>
        </div>
      );
    },
    table({ children }) {
      let el: HTMLTableElement | null = null;
      const csv = () => el ? [...el.rows].map((r) => [...r.cells].map((c) => `"${c.innerText.replace(/"/g, '""')}"`).join(",")).join("\n") : "";
      return (
        <div className="md-table">
          <table ref={(t) => { el = t; }}>{children}</table>
          <div className="md-table-f"><CopyBtn get={csv} label="Copy as CSV" /></div>
        </div>
      );
    },
    a({ href, children }) {
      if (href && /^\/(?!\/)/.test(href)) return <Link to={href}>{children}</Link>;
      return <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>;
    },
    img({ alt }) { return <span className="weak">[image not shown{alt ? `: ${alt}` : ""}]</span>; },
  };
}

export default function ShimMarkdown({ text, done = true }: { text: string; done?: boolean }) {
  const comps = useMemo(() => components(done), [done]);
  return (
    <div className="md">
      <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { languages: LANGS, detect: false, ignoreMissing: true }]]} components={comps}>
        {text}
      </Markdown>
    </div>
  );
}
