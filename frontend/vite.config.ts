import { defineConfig } from "vite";

export default defineConfig({
  // Read VITE_* vars from the repo-root .env (same file the backend uses).
  envDir: "..",
  server: { port: 5173 },
});
