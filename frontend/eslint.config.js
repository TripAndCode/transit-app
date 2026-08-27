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
      // optimize), on top of the classic rules-of-hooks set.
      ...reactHooks.configs['recommended-latest'].rules,
      // 'recommended-latest' leaves the compiler-bailout diagnostic ('todo')
      // off by default. With manual useMemo/useCallback/React.memo banned
      // below, a silent bailout would otherwise leave code unmemoized with
      // no lint signal at all — promote it to error so bailouts are visible.
      'react-hooks/todo': 'error',
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
          selector: "CallExpression[callee.name='useMemo']",
          message: 'Do not use useMemo — the React Compiler handles memoization automatically. Inline the computation.',
        },
        {
          selector: "CallExpression[callee.name='useCallback']",
          message: 'Do not use useCallback — the React Compiler handles memoization automatically. Use a plain function.',
        },
        {
          selector: "CallExpression[callee.name='memo']",
          message: 'Do not use React.memo — the React Compiler handles memoization automatically.',
        },
        {
          // Catches the member-expression form of the two hooks
          // (`React.useMemo(...)`/`React.useCallback(...)`) that the
          // bare-identifier CallExpression selectors above miss. A
          // MemberExpression selector (rather than
          // CallExpression[callee.object...]) also catches `React.memo`
          // passed by reference, not just called.
          selector: "MemberExpression[object.name='React'][property.name=/^(useMemo|useCallback|memo)$/]",
          message:
            'Do not use React.useMemo/React.useCallback/React.memo — the React Compiler handles memoization automatically.',
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
    },
  },
)
