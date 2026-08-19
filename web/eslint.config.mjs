// ESLint flat config for the StepWise frontend.
//
// `next lint` was the script here for months, but ESLint was never actually
// installed and there was no config, so running it only ever opened the
// interactive setup wizard. Nothing was linted, and CI never called it — while
// the backend has had `ruff` enforced from the start.
//
// `eslint-config-next` still ships eslintrc-style configs only (no flat
// export as of 15.5), so FlatCompat translates them. That is the same shape
// `create-next-app` generates for Next 15, deliberately, so this stays
// recognisable rather than clever.

import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";
import prettier from "eslint-config-prettier";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

const config = [
  {
    // Build output and test artefacts. `ignores` in a config object with no
    // other keys is global, which is what makes it apply before any file is
    // parsed rather than merely suppressing its findings.
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "test-results/**",
      "playwright-report/**",
      "next-env.d.ts",
      "*.tsbuildinfo",
    ],
  },

  // next/core-web-vitals: the Next rules plus react and jsx-a11y, with the
  // Core Web Vitals subset escalated to errors.
  // next/typescript: @typescript-eslint's recommended set and the TS parser.
  ...compat.extends("next/core-web-vitals", "next/typescript"),

  {
    rules: {
      // The reason this config exists at all. The codebase gained several
      // useEffect/useCallback/useRef hooks, and a wrong dependency array there
      // is a silent correctness bug — a stale closure, not a style nit. The
      // shipped default for exhaustive-deps is "warn", which in CI is
      // indistinguishable from passing; both are errors here.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",

      // An unused parameter named with a leading underscore is a deliberate
      // signature placeholder, not an oversight.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },

  {
    // Tests reach into internals and stub globals; `any` in a mock is not the
    // same defect as `any` in application code.
    files: ["tests/**/*.{ts,tsx}", "*.config.{ts,mts,mjs}"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },

  // Last: switches off the stylistic rules Prettier owns, so the two tools
  // cannot disagree about the same line.
  prettier,
];

export default config;
