/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API = process.env.AEGIS_API_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: false },
      "/ws": { target: API.replace("http", "ws"), ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
