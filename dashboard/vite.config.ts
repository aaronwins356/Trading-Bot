import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the dashboard runs on :5173 and proxies the API (python: `tradebot serve`) on :8080.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", ws: true, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
