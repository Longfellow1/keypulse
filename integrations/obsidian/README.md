# KeyPulse Obsidian Integration

This folder documents the local bridge between KeyPulse and an Obsidian vault.

## How it works

1. KeyPulse captures activity into SQLite.
2. `keypulse export --format obsidian --output <vault-path>` writes markdown notes.
3. Obsidian indexes the notes through `Properties`, `Backlinks`, and `Bases`.

## Automation

- The default daily sync runs at `09:05`.
- The scheduled command is `keypulse obsidian sync`.
- By default it exports yesterday's data so the vault is updated with the previous day once the morning starts.

## Vault contract

- Generated notes are written to `Daily/`, `Events/`, and `Topics/`.
- Notes are local markdown files with YAML frontmatter.
- No cloud sync is required for the bridge itself.

## Recommended loop

- Capture all day.
- Let the daily sync run once per morning.
- Review event cards weekly.
- Promote stable patterns into topic cards and evergreen notes.
