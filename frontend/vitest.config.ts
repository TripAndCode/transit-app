import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react({ babel: { plugins: ["babel-plugin-react-compiler"] } })],
  test: {
    environment: "jsdom",
    globals: true,
    // The .cjs shim first: `src/lint/eslintReactCompilerBans.test.ts` loads
    // the real flat config, which pulls in typescript-eslint, which throws on
    // TypeScript 7. A vite `resolve.alias` cannot reach it -- the package is
    // externalized CJS, loaded through Node's own require rather than vite's
    // transform -- so the same module-resolution rewrite the lint scripts
    // apply has to run inside the worker. See that file for why it exists.
    setupFiles: ["./scripts/ts6-for-eslint.cjs", "./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // jsdom's CSS-color dependency ships ESM that the default `forks` pool
    // can't `require()` under Node; the worker-thread pool loads it cleanly.
    pool: "threads",
    // The default (5s) leaves no margin under machine load for the handful
    // of tests that drive several real userEvent interactions against a
    // provider-wrapped tree in one case; a slow CI runner or a busy dev
    // machine pushed those past 5s even though nothing was actually hung.
    testTimeout: 15000,
    // Provided by the `@vitest/coverage-v8` dev dependency (see
    // package.json), matching this repo's vitest ^4.1.8.
    coverage: {
      provider: "v8",
      reporter: ["text-summary"],
      thresholds: { lines: 70, statements: 70 },
    },
  },
});
