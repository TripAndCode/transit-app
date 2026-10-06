/** How long Testing Library's `findBy*` and `waitFor` wait before failing.
 *  Its 1 s default is shorter than a mocked query can take to settle when
 *  several suites share a loaded machine, so tests failed that had nothing
 *  wrong with them. Well under vitest's `testTimeout` (vitest.config.ts), so
 *  a test with a few waits still finishes, or fails, inside its own limit;
 *  only a wait that is going to fail pays the difference. */
export const ASYNC_UTIL_TIMEOUT_MS = 5000;
