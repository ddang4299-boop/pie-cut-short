import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The FastAPI backend (uvicorn app.main:app) listens on :8000; the UI calls it as /api/*.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: BACKEND_URL,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    // The default forks/threads pools fail here ("Cannot read properties of undefined (reading 'config')"
    // in every file, Node 24.22 + Vitest 5.1.1); vmThreads runs the same tests fine.
    pool: "vmThreads",
  },
});
