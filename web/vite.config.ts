import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.SHIMPAKU_API ?? "http://127.0.0.1:8780";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: api, changeOrigin: false } } },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
});
