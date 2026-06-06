import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // jsdom's CSS-color dependency ships ESM that the default `forks` pool
    // can't `require()` under Node; the worker-thread pool loads it cleanly.
    pool: "threads",
  },
});
