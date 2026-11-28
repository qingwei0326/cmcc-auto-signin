import js from '@eslint/js';
import globals from 'globals';

const baseRules = {
  ...js.configs.recommended.rules,
  'no-undef': 'error',
  'no-redeclare': 'error',
  'no-unused-vars': ['error', { vars: 'local', args: 'none', caughtErrors: 'none' }],
  'no-empty': ['error', { allowEmptyCatch: true }],
  'no-eval': 'error',
  'no-implied-eval': 'error',
  'no-irregular-whitespace': ['error', { skipStrings: true, skipTemplates: true, skipComments: true }],
  'no-restricted-syntax': [
    'error',
    {
      selector: "AssignmentExpression[left.type='MemberExpression'][left.property.name='innerHTML']",
      message: '禁止直接写 .innerHTML，请使用 core.js 的 setHtml(el, html`...`)',
    },
    {
      selector: "AssignmentExpression[left.type='MemberExpression'][left.property.name='outerHTML']",
      message: '禁止直接写 .outerHTML，请使用 core.js 的 setHtml(el, html`...`)',
    },
    {
      selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
      message: '禁止调用 insertAdjacentHTML，请使用 setHtml(el, html`...`)',
    },
    {
      selector: "CallExpression[callee.object.name='document'][callee.property.name='write']",
      message: '禁止调用 document.write',
    },
    {
      selector: "MemberExpression[object.property.name='style'][property.name='display']",
      message: '禁止直接操作 .style.display，请使用 setHidden(el, hide) 或 CSS class',
    },
    {
      selector: "MemberExpression[object.property.name='style'][property.name='color']",
      message: '禁止直接操作 .style.color，请使用 setTone(el, tone) 或 CSS class',
    },
  ],
};

export default [
  {
    files: ['static/js/**/*.js', 'static/app.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
      },
    },
    rules: baseRules,
  },
  {
    files: ['tests/frontend/**/*.mjs'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.node,
        ...globals.browser,
      },
    },
    rules: baseRules,
  },
];
