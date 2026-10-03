import { defineConfig } from "vitest/config";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import babel from "@rolldown/plugin-babel";

export default defineConfig({
  plugins: [react(), babel({ presets: [reactCompilerPreset()] })],
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
    // Node 25+ enables its own global localStorage and sessionStorage
    // (localStorage is undefined without --localstorage-file), and the jsdom
    // environment keeps a global it already finds, so tests would never see
    // jsdom's Storage. Turning the Node feature off hands both back to jsdom;
    // on Node 22 and 24 the feature is already off and the flag is a no-op.
    execArgv: ["--no-experimental-webstorage"],
    // The default (5s) leaves no margin under machine load for the handful
    // of tests that drive several real userEvent interactions against a
    // provider-wrapped tree in one case; a slow CI runner or a busy dev
    // machine pushed those past 5s even though nothing was actually hung.
    testTimeout: 15000,
    // A `vi.stubGlobal` is undone after the test that made it. Several tests
    // replace `IntersectionObserver`/`ResizeObserver` with a driveable stub,
    // or force one absent; without this those replacements outlive the test
    // and the next one silently inherits them.
    unstubGlobals: true,
    // CI's frontend job runs the suite as `test:coverage`, so these
    // thresholds gate every pull request. With no `include`, only files some
    // test loads are measured: a module no test imports lowers nothing.
    coverage: {
      // `@vitest/coverage-v8` must stay on vitest's exact version: across a
      // major they disagree on the coverage payload and every test file errors
      // at collection, which is why `.github/dependabot.yml` groups them.
      provider: "v8",
      reporter: ["text-summary"],
      thresholds: { lines: 70, statements: 70 },
    },
  },
});
