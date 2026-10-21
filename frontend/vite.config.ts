import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Saat development, /api dan /evidence diteruskan ke FastAPI di port 8000.
// changeOrigin: false mempertahankan header Host (localhost:5173) supaya pemeriksaan Origin di server lolos
// tanpa perlu ALLOWED_ORIGINS.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
      "/evidence": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
});
