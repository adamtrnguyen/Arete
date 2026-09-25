# Arete skills for coding agents

Curated agent skills that ship with Arete, versioned with the code they describe. Each is
a folder with a `SKILL.md` (loaded when its description matches the task) and optional
`references/` read on demand.

| Skill | Use it for |
|---|---|
| [`arete`](arete/SKILL.md) | Drafting cards into notes, showing them live in Obsidian's Card Editor, syncing to Anki, graph health, fixing cards flagged in review, study queues, vault maintenance. References: [schema](arete/references/SCHEMA.md), [YAML format guide](arete/references/FORMAT.md), [troubleshooting](arete/references/TROUBLESHOOTING.md). |

## Install

Link a skill rather than copying it, so it updates with Arete:

```bash
# for one vault (Claude Code opened in the vault)
ln -s /path/to/arete/skills/arete "<vault>/.claude/skills/arete"

# for every project
ln -s /path/to/arete/skills/arete ~/.claude/skills/arete
```

## Curating

- **A skill earns a place here** only if it is about using Arete, not about one person's vault.
- **Every command and flag in a skill must exist**: check against `arete <cmd> --help`.
- **Mark measured claims** (📏 in `FORMAT.md`) and re-measure when the converter changes.
- **Update `TROUBLESHOOTING.md`** when a fix ships: say which version fixed it.
