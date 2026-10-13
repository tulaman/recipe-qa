import { defineConfig } from "vite";

// In development the API runs on :8000 (uvicorn); in production the same
// container serves both, so the UI always calls same-origin paths.
const api = "http://localhost:8000";

export default defineConfig({
  server: {
    proxy: { "/ask": api, "/healthz": api, "/schema": api },
  },
});
