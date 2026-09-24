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
      // at 'warn'; `npm run lint` runs with `--max-warnings 0`, so a
      // bailout now fails the build instead of passing silently.
      // `react-hooks/todo` ("unimplemented compiler features", Hint
      // severity, off by default upstream) is deliberately left at its
      // default: it isn't a bailout diagnostic, and promoting an
      // off-by-default rule needs its own verified-clean lint run first.
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
      // `any` defeats the type checker; every existing use has been
      // replaced with a real type, so this now blocks on new ones.
      '@typescript-eslint/no-explicit-any': 'error',
      // React Compiler (enabled repo-wide, see CLAUDE.md) auto-memoizes —
      // manual useMemo/useCallback/React.memo are redundant at best and can
      // mask compiler bailouts at worst. Banned as a hard error; use
      // useEffectEvent for fresh-props-in-stable-handlers instead (see
      // MapTab).
      'no-restricted-syntax': [
        'error',
        {
          // Bare-identifier form only; the member-expression form
          // (`React.useMemo`/`.useCallback`/`.memo`, any receiver, and
          // literal computed access like `React["useMemo"]`) is fully
          // covered by no-restricted-properties below — a separate
          // MemberExpression selector here would just double-report the
          // same violation.
          selector: "CallExpression[callee.name=/^(useMemo|useCallback|memo)$/]",
          message:
            'Do not use useMemo/useCallback/React.memo — the React Compiler handles memoization automatically. Inline the computation or use a plain function.',
        },
        {
          // A raw number (or any other literal) assigned to a `zIndex`
          // object property bypasses the shared stacking-order ladder in
          // src/styles/zIndex.ts — nothing else then tells you where it
          // sits relative to every other overlay. `zIndex: Z_INDEX.foo` (a
          // MemberExpression, not a Literal) is unaffected by this
          // selector, as is a derived expression like `Z_INDEX.foo - 1`.
          selector: 'Property[key.name="zIndex"][value.type="Literal"]',
          message: "Do not hardcode zIndex — use a rung from Z_INDEX (src/styles/zIndex.ts) instead.",
        },
        {
          // A local binding named `window` or `document` shadows the DOM
          // global of the same name for the whole of its scope, so every
          // later reference there resolves to the local value instead. The
          // mistake is invisible until something in that scope wants the real
          // global (a `window.matchMedia` call, a `document.querySelector`),
          // at which point it fails at runtime far from its cause. Name the
          // local for what it holds instead.
          selector: 'VariableDeclarator[id.name=/^(window|document)$/]',
          message:
            'Do not name a local binding `window` or `document` — it shadows the DOM global for the rest of the scope. Use a descriptive name (e.g. `viewWindow`).',
        },
        {
          // The same hazard introduced through a parameter, which the
          // VariableDeclarator selector above cannot see. A destructured or
          // rest parameter is not an `Identifier` in `params` and so is out of
          // reach of this selector, but it also cannot bind the bare names
          // `window`/`document` without a property alias that reads as the
          // shadow it is.
          selector:
            ':matches(FunctionDeclaration, FunctionExpression, ArrowFunctionExpression, TSDeclareFunction, TSFunctionType, TSMethodSignature) > Identifier.params[name=/^(window|document)$/]',
          message:
            'Do not name a parameter `window` or `document` — it shadows the DOM global for the whole function body. Use a descriptive name (e.g. `viewWindow`).',
        },
        {
          // `Number.prototype.toLocaleString`/`Date.prototype.toLocaleDateString`/
          // `toLocaleTimeString`/`toLocaleString` silently default to the
          // runtime's locale rather than the active UI language, so ja/en
          // users can see numbers or dates formatted in the wrong locale.
          selector: 'CallExpression[callee.property.name=/^toLocale(String|DateString|TimeString)$/]',
          message: 'Do not call toLocale*() directly — use formatNumber()/formatDateTime() from src/utils/format.ts, which read the active UI language.',
        },
      ],
      // Closes the aliased-import hole the syntax selectors above can't see
      // (e.g. `import { useMemo as m } from "react"`). Only matches *named*
      // imports (`importNames`) — a namespace import (`import * as React
      // from "react"`) isn't targeted by name, so this rule instead flags
      // the whole `react` module the moment ANY of useMemo/useCallback/memo
      // exist among its exports, regardless of whether the importing file
      // actually uses them. No file uses `import * as React` today (grepped
      // clean), so this causes no false positive now, but a future
      // namespace import (e.g. for `React.forwardRef`) would fail here with
      // a misleading "do not import useMemo/useCallback/memo" message even
      // if it never touches them.
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
      // *literal* computed property access (`React["useMemo"]`) — all
      // otherwise slip past no-restricted-imports (a default specifier
      // resolves to the name `"default"`, which isn't in `importNames`).
      // Receiver-agnostic by design, since the property name itself is the
      // signal; verified no existing `.memo`/`.useMemo`/`.useCallback`
      // property access exists in frontend/src today, so this introduces no
      // false positive. Does NOT catch a *dynamically computed* property
      // name (e.g. `x["use" + "Memo"]`) — an inherent ESLint static-analysis
      // limitation, not closeable without a custom scope-aware rule; this
      // requires deliberate obfuscation to hit, not an easy accidental
      // route-around. Matches the property name on ANY receiver, not just
      // React imports — an unrelated future `.memo`/`.useMemo`/
      // `.useCallback` property (e.g. an unrelated memoization-cache object
      // or a GraphQL field literally named `memo`) would also trip this.
      // No such usage exists today; if one is legitimately needed later,
      // scope this rule to a receiver check or add a targeted
      // eslint-disable with a one-line reason at that call site.
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
  {
    // The entry module bootstraps the app through createRoot and exports
    // nothing on purpose, so Fast Refresh never applies to it. The rule's
    // advice there ("move your components to a separate file") is about a
    // capability this file cannot have.
    files: ['src/main.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
)
