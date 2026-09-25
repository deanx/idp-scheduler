import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the local console so the browser only
// ever talks to one origin — which is why the backend's CORS stays off
// by default. `--dev-cors` on the backend is the fallback for when this
// proxy is not in the path.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
