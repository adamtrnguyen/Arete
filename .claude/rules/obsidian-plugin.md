---
paths:
  - "obsidian-plugin/**"
---

# Obsidian Plugin Rules

- TypeScript 5, esbuild bundler
- Same DDD layers as Python backend: `domain/`, `infrastructure/`, `application/`, `presentation/`
- Tests: Jest. Lint: ESLint + Prettier.
- Build: `just build-obsidian`
- Test: `just test-obsidian`
- Dev: `just dev-plugin` (esbuild watcher)
