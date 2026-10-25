// Virga's mark: a cloud whose rain streaks fade before they reach the ground (virga is rain that
// evaporates on the way down). Original drawing.
export default function VirgaMark({ size = 16, title }: { size?: number; title?: string }) {
  const id = "vg-fade";
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" role={title ? "img" : undefined} aria-hidden={title ? undefined : true} className="virga-mark">
      {title && <title>{title}</title>}
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="currentColor" stopOpacity="1" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d="M7 13.5a4 4 0 0 1-.4-7.98A5.5 5.5 0 0 1 17.3 6.6 3.5 3.5 0 0 1 17.5 13.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      {[8, 12, 16].map((x, i) => <path key={x} d={`M${x} ${15.5 + (i % 2)}l-1.6 ${6 - (i % 2) * 1.5}`} stroke={`url(#${id})`} strokeWidth="1.7" strokeLinecap="round" />)}
    </svg>
  );
}
