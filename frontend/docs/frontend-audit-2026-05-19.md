# Frontend audit and remediation plan (2026-05-19)

## 1) Incident under investigation

**Error:**

`[plugin:vite:import-analysis] Failed to resolve import "./styles/legacy.css" from "src/main.tsx"`

### Root cause

- `src/main.tsx` attempts dynamic import of `./styles/legacy.css` when `VITE_ENABLE_LEGACY_CSS === "true"`.
- File `src/styles/legacy.css` is missing from the repository.
- Existing audit `src/styles/legacy-selector-usage.md` indicates legacy selectors are deprecated and replaced.

### Risk

- Dev server crashes with Vite overlay when env var enabled.
- Optional legacy toggle introduces runtime/build fragility and dead configuration path.

## 2) Fix strategy for this error

### Recommended (primary)

1. Remove dead legacy CSS feature flag import path from `main.tsx`.
2. Keep only `index.css` + current modular styles.
3. Add a regression check in CI/dev (`npm run build`) to ensure no unresolved imports.

### Alternative (not recommended)

- Add empty `legacy.css` to satisfy import. This hides configuration debt and can mislead future maintenance.

## 3) Broader frontend risk audit (likely adjacent issues)

### A. Import & asset integrity

- Check for unresolved relative imports across `src`.
- Check for stale assets referenced from CSS/TSX.
- Validate path case-sensitivity (Linux CI catches macOS-hidden issues).

### B. Styling architecture drift

- Validate no references to removed legacy selectors/classes.
- Ensure style ownership boundaries: `base.css`, `chat-workspace.css`, component styles.
- Add lint rule or script to detect imports of non-existent style files.

### C. Environment-variable feature flags

- Inventory all `import.meta.env.*` usage.
- Remove obsolete flags and document active flags in README.
- Define safe defaults for missing variables.

### D. Type & API contracts

- Run strict TS typecheck and verify `src/types/api.ts` consistency with usage.
- Confirm React Query hooks have deterministic error/loading handling.

### E. Test coverage gaps

- Ensure at least one smoke test that boots app shell and major routes.
- Ensure chat workspace happy-path test remains green.
- Add a test that fails on unresolved CSS import regressions (build smoke in CI).

### F. Build pipeline hardening

- Add/keep mandatory CI sequence:
  1. `npm run lint`
  2. `npm run test -- --run`
  3. `npm run build`
- Treat unresolved-import warnings as failures.

## 4) Implementation plan (phased)

### Phase 0 — Immediate unblock (today)

- [x] Remove `legacy.css` dynamic import block from `src/main.tsx`.
- [x] Run build to validate fix.

### Phase 1 — Static integrity sweep

- [ ] Run unresolved import scan (TS + Vite build).
- [ ] Validate no dead CSS imports and no broken static assets.

### Phase 2 — Flag/config cleanup

- [ ] Remove deprecated frontend env vars from docs/config templates.
- [ ] Add short “active env vars” section to frontend README.

### Phase 3 — Quality gates

- [ ] Ensure lint/test/build enforced in CI before merge.
- [ ] Add lightweight smoke e2e route boot check.

### Phase 4 — Observability & DX

- [ ] Keep Vite overlay enabled in dev (do not disable) to surface real failures.
- [ ] Add troubleshooting section for common frontend boot errors.

## 5) Acceptance criteria

- App starts locally with default env and with all documented feature flags.
- `npm run build` passes without unresolved import errors.
- No reference to `legacy.css` remains.
- CI fails fast on any future unresolved import.
