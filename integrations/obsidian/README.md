# KeyPulse Obsidian Integration

This folder documents the local bridge between KeyPulse and an Obsidian vault.

For product-level workflow guidance, see [docs/obsidian-workflow.md](../../docs/obsidian-workflow.md).

## How it works

1. KeyPulse captures activity into SQLite.
2. Privacy filters run before sensitive content is persisted.
3. `keypulse obsidian sync` writes markdown notes.
4. Obsidian indexes the notes through Properties, backlinks, Bases, or Dataview.

## Automation

- The default daily sync runs at `09:05`.
- The scheduled command is `keypulse obsidian sync`.
- By default it exports yesterday's data so the vault is updated with the previous day once the morning starts.

## Vault contract

- Generated notes are written to `Daily/`, `Events/`, `Topics/`, and `Anchors/`.
- Notes are local markdown files with YAML frontmatter.
- No cloud sync is required for the bridge itself.
- Generated notes should remain readable even if KeyPulse is not running.
- User-authored sections should not be overwritten by incremental sync.

## Recommended loop

- Capture all day.
- Let the daily sync run once per morning.
- Review event cards weekly.
- Promote stable patterns into topic cards and evergreen notes.
- Promote repeated topics into anchors when they describe a durable personal method.
