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
      ...reactHooks.configs.recommended.rules,
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
    },
  },
)
