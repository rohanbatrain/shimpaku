import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import zlib from "node:zlib";

const api = process.env.SHIMPAKU_API ?? "http://127.0.0.1:8780";

// Pre-compress text assets so Caddy can serve them with `file_server precompressed`.
// node:zlib only (no plugin dependency); .zst is emitted when this Node has zstd (>= 22.15).
function precompress(): Plugin {
  const TEXT = /\.(js|mjs|css|html|svg|json|txt|map)$/;
  let outDir = "dist";
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
  return {
    name: "shimpaku-precompress",
    apply: "build",
    configResolved(c) {
      outDir = c.build.outDir;
    },
    closeBundle() {
      for (const f of walk(outDir).filter((f) => TEXT.test(f))) {
        const buf = readFileSync(f);
        if (buf.length < 1024) continue;
        writeFileSync(`${f}.br`, zlib.brotliCompressSync(buf, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 11 } }));
        writeFileSync(`${f}.gz`, zlib.gzipSync(buf, { level: 9 }));
        if (typeof zlib.zstdCompressSync === "function")
          writeFileSync(`${f}.zst`, zlib.zstdCompressSync(buf, { params: { [zlib.constants.ZSTD_c_compressionLevel]: 19 } }));
      }
    },
  };
}

// Bundle report (not a dependency): npx vite-bundle-visualizer, or add rollup-plugin-visualizer locally.
export default defineConfig({
  plugins: [
    react(),
    precompress(),
  ],
  server: { proxy: { "/api": { target: api, changeOrigin: false } } },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 600 },
});
