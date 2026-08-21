/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
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
  // `npm test`. Unit tests live next to what they cover, as *.test.ts(x).
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.{ts,tsx}"],
    // A parser that stops making progress would pin the worker: a synchronous
    // infinite loop cannot be interrupted by testTimeout. The scanners carry their
    // own progress budgets (see Markdown.tsx); this bounds everything else.
    testTimeout: 5000,
    hookTimeout: 5000,
  },
});
