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
