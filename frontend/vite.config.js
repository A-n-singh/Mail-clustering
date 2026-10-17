import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api/* is proxied to the FastAPI backend so the session cookie is same-origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") } } },
});
