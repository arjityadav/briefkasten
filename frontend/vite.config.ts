import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built into the Python package, which serves it at "/". In dev, `npm run dev` proxies the API
// to `briefkasten serve` on port 8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/briefkasten/web/dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
