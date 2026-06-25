import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev (`npm run dev`) Vite serves the UI and proxies API/WS calls to the Rust
// runtime on :8000. In prod the runtime serves the built `dist/` itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/upload": "http://127.0.0.1:8000",
      "/preview": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
