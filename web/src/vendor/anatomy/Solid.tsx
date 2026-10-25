// Vendored from anatomy (https://github.com/wheresryan22/anatomy), kit/react/draw.tsx `Solid` at
// commit 1482e8469b. MIT licence, see ./LICENSE: Copyright (c) 2026 Ryan.
// Adapted: React import style, and `title` / extra props pass through to the group.
import type { ReactNode, SVGProps } from "react";
import type { SolidPaths, Tone } from "./iso-kit";

type SolidProps = {
  paths: SolidPaths;
  tone?: Tone;
  crease?: Tone | "none";
  halo?: boolean;
  lit?: boolean;
  flat?: boolean;
  className?: string;
  inner?: ReactNode;
  children?: ReactNode;
} & Omit<SVGProps<SVGGElement>, "children" | "className">;

export function Solid({ paths, tone = "hi", crease = "faint", halo = false, lit = false, flat = false, className, inner, children, ...rest }: SolidProps) {
  return (
    <g className={className ? `iso-solid ${className}` : "iso-solid"} data-lit={lit ? "" : undefined} data-seamless={paths.seamless ? "" : undefined} {...rest}>
      {halo ? <path className="iso-halo" d={paths.fill} /> : null}
      <path className="iso-fill" d={paths.fill} />
      {flat ? null : paths.shades.map((d, shade) => (d ? <path key={shade} className="iso-shade" data-shade={shade} d={d} /> : null))}
      {flat ? null : <path className="iso-top" d={paths.top} />}
      {crease === "none" || !paths.crease ? null : <path className="iso-line iso-crease" data-tone={crease} d={paths.crease} />}
      {crease === "none" || !paths.bevel ? null : <path className="iso-line iso-bevel" data-tone={crease} d={paths.bevel} />}
      {inner}
      <path className="iso-line iso-edge" data-tone={tone} d={paths.outline} />
      {children}
    </g>
  );
}
