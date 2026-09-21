import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react({ babel: { plugins: ["babel-plugin-react-compiler"] } })],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // jsdom's CSS-color dependency ships ESM that the default `forks` pool
    // can't `require()` under Node; the worker-thread pool loads it cleanly.
    pool: "threads",
    // Requires the `@vitest/coverage-v8` package (matching this repo's
    // vitest ^4.1.8), which is not installed in this worktree — left
    // commented rather than enabled so `npm run test:coverage` fails with
    // vitest's own "install @vitest/coverage-v8" message instead of a
    // config-shape error once the dependency lands.
    // coverage: {
    //   provider: "v8",
    //   reporter: ["text-summary"],
    //   thresholds: { lines: 70, statements: 70 },
    // },
  },
});
