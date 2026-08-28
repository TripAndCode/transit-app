import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import tseslint from 'typescript-eslint'

// Accessibility: adopt jsx-a11y's `recommended` preset, which enables the
// meaningful rules with their intended options (and deliberately leaves
// deprecated/superseded rules such as `label-has-for` off in favour of
// `label-has-associated-control`). The rollout is now complete, so the rules
// are enforced as errors and gate the build.
const a11yAsError = Object.fromEntries(
  Object.entries(jsxA11y.flatConfigs.recommended.rules).map(([rule, value]) => {
    // Preserve rules the preset intentionally disables (e.g. the deprecated
    // `label-has-for`); only promote the active rules to `error`.
    const severity = Array.isArray(value) ? value[0] : value
    if (severity === 'off' || severity === 0) return [rule, value]
    const options = Array.isArray(value) ? value.slice(1) : []
    return [rule, ['error', ...options]]
  }),
)

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'node_modules'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      // 'recommended-latest' includes the React Compiler diagnostics shipped
      // with eslint-plugin-react-hooks v7 (flags code the compiler can't
      // optimize), on top of the classic rules-of-hooks set. Its actual
      // bailout signals (`unsupported-syntax`, `incompatible-library`) ship
      // at 'warn', and `npm run lint` is bare `eslint .` with no
      // `--max-warnings` — so a warn-level bailout doesn't fail the build
      // today. `react-hooks/todo` ("unimplemented compiler features", Hint
      // severity, off by default upstream) was previously promoted to
      // 'error' here as an attempted bailout signal — removed: it isn't
      // actually a bailout diagnostic, and promoting an unverified
      // off-by-default rule risks failing lint on unrelated files with no
      // lint run available in this sandbox to confirm it's clean. Needs a
      // human to either add `--max-warnings 0` to `frontend/package.json`'s
      // `lint` script, or promote `unsupported-syntax`/`incompatible-library`
      // to `error` after a verified clean `npm run lint` run.
      ...reactHooks.configs['recommended-latest'].rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      ...a11yAsError,
      // Honor the underscore-prefix convention for intentionally-unused bindings.
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // `any` is a code smell, not a correctness bug — surface it as a warning
      // during adoption rather than blocking on the existing uses.
      '@typescript-eslint/no-explicit-any': 'warn',
      // React Compiler (enabled repo-wide, see CLAUDE.md) auto-memoizes —
      // manual useMemo/useCallback/React.memo are redundant at best and can
      // mask compiler bailouts at worst. Banned as a hard error; use
      // useEffectEvent for fresh-props-in-stable-handlers instead (see
      // MapTab).
      'no-restricted-syntax': [
        'error',
        {
          // Bare-identifier form only; the member-expression form
          // (`React.useMemo`/`.useCallback`/`.memo`, any receiver, computed
          // or not) is fully covered by no-restricted-properties below —
          // a separate MemberExpression selector here would just
          // double-report the same violation.
          selector: "CallExpression[callee.name=/^(useMemo|useCallback|memo)$/]",
          message:
            'Do not use useMemo/useCallback/React.memo — the React Compiler handles memoization automatically. Inline the computation or use a plain function.',
        },
      ],
      // Closes the aliased-import hole the syntax selectors above can't see
      // (e.g. `import { useMemo as m } from "react"`).
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'react',
              importNames: ['useMemo', 'useCallback', 'memo'],
              message:
                'Do not import useMemo/useCallback/memo — the React Compiler handles memoization automatically. Use useEffectEvent for fresh-props-in-stable-handlers.',
            },
          ],
        },
      ],
      // Catches every member-expression form of the ban: the conventional
      // `React.useMemo(...)`/`.useCallback(...)`/`.memo`, a default-import
      // alias (`import Reakt from "react"; Reakt.useMemo(...)`), and
      // computed property access (`React["useMemo"]`) — all otherwise slip
      // past no-restricted-imports (a default specifier resolves to the name
      // `"default"`, which isn't in `importNames`). Receiver-agnostic by
      // design, since the property name itself is the signal; verified no
      // existing `.memo`/`.useMemo`/`.useCallback` property access exists in
      // frontend/src today, so this introduces no false positive.
      'no-restricted-properties': [
        'error',
        {
          property: 'useMemo',
          message: 'Do not use useMemo — the React Compiler handles memoization automatically. Inline the computation.',
        },
        {
          property: 'useCallback',
          message: 'Do not use useCallback — the React Compiler handles memoization automatically. Use a plain function.',
        },
        {
          property: 'memo',
          message: 'Do not use React.memo — the React Compiler handles memoization automatically.',
        },
      ],
    },
  },
)
