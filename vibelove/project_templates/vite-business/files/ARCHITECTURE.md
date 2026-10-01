# Architecture

- frontend/: React + TypeScript + Vite. No backend is required.
- src/components/: reusable layout, table, sheet and confirmation dialog.
- src/App.tsx: customer example, search/filter and CRUD orchestration.
- src/data.ts: typed repository and browser-local persistence. Replace this
  boundary with an API when needed. There is no authentication or shared database.
- src/styles.css: responsive design tokens and component styles.
- public/brand.svg: application mark.
- Install: cd frontend && npm ci --ignore-scripts
- Verify: cd frontend && npm run build (TypeScript check plus production bundle).
- Dev: cd frontend && npm run dev. Vibelove supplies the preview port via CLI.
- Read DESIGN.md and affected files before editing. Keep existing dependencies
  and reusable components unless the requested feature needs a change.
