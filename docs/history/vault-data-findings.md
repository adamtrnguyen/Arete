# Findings about the real vault

Measurements moved here from CLAUDE.md on 2026-11-04. CLAUDE.md keeps the rule and the
open-work item.

## The stale OneDrive copy

`~/Library/CloudStorage/OneDrive-Personal/Obsidian Vault` is also a git repo. When
checked, its `.git` last moved 2026-08-29, against 2026-09-10 for the live vault
(`~/Obsidian Vault`). Edit the live one.

## Duplicate Arete ids

`arete vault check` does not detect a duplicate Arete id. A run against the real vault,
978 notes at the time, found three.
