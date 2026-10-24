// Original Shimpaku theme on plain Grommet (no grommet-theme-hpe). Primary #01A982 per CONTRACT D3.
// #01A982 is ~3:1 on white, so it is used for fills/marks with dark ink on top; text accents use a
// darker step (#00785C, 5.2:1) to hold WCAG AA.
import type { ThemeType } from "grommet";

export const c = {
  primary: "#01A982",
  primaryInk: "#00785C",
  onPrimary: "#04261B",
  nav: "#0E1A16",
  navHover: "#16271F",
  navActive: "#1B3329",
  navText: "#C9D8D1",
  navMuted: "#8FA79C",
  back: "#F3F5F4",
  front: "#FFFFFF",
  line: "#DCE2DF",
  lineStrong: "#C5CEC9",
  text: "#16211C",
  weak: "#56645E",
  ok: "#17702F",
  warn: "#8A5B00",
  warnFill: "#D99A06",
  crit: "#B3261E",
  critFill: "#D93A30",
  info: "#1D6E8C",
  infoFill: "#3A9CC0",
  unknown: "#6F7B76",
  slate: "#5B6B8C",
};

export const theme: ThemeType = {
  global: {
    font: { family: "Inter, system-ui, sans-serif", size: "14px", height: "20px" },
    colors: {
      brand: c.primary,
      focus: c.primary,
      text: { light: c.text },
      "text-weak": c.weak,
      border: c.line,
      "background-back": c.back,
      "background-front": c.front,
      "status-ok": c.ok,
      "status-warning": c.warnFill,
      "status-critical": c.critFill,
      "status-unknown": c.unknown,
      control: c.primary,
    },
    edgeSize: { xxsmall: "2px", xsmall: "4px", small: "8px", medium: "16px", large: "24px", xlarge: "32px" },
    control: { border: { radius: "6px", color: c.lineStrong } },
    input: { padding: "7px 10px", font: { weight: 400, size: "14px" } },
  },
  button: {
    border: { radius: "6px" },
    padding: { vertical: "6px", horizontal: "14px" },
    default: { border: { color: c.lineStrong, width: "1px" }, color: c.text, background: { color: c.front }, font: { weight: 500 } },
    primary: { background: { color: c.primary }, color: c.onPrimary, border: { color: c.primary, width: "1px" }, font: { weight: 600 } },
    hover: { default: { background: { color: "#EEF2F0" } }, primary: { background: { color: "#00BF93" } } },
    size: { small: { border: { radius: "6px" }, pad: { vertical: "4px", horizontal: "10px" } } },
  },
  dataTable: {
    header: { font: { weight: "600" }, color: c.weak, extend: "font-size:12px;text-transform:uppercase;letter-spacing:.05em;" },
    pinned: { header: { background: { color: c.front } } },
  },
  formField: { border: { color: c.lineStrong, side: "all" }, round: "6px", label: { size: "13px", weight: 600, margin: { horizontal: "none", bottom: "4px" } } },
};
