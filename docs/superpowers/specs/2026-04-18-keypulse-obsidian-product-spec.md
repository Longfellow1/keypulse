# KeyPulse + Obsidian Product Spec

**Goal:** Build a local-first personal memory pipeline where KeyPulse captures activity and automatically binds to the best local knowledge sink, defaulting to Obsidian when present and falling back to a standalone vault when it is not.

## Product Shape

The system is not a timeline app. Time is only an index. The primary objects are:

- `事件` - the smallest archivable fragment that carries evidence, a question, or a conclusion.
- `主题` - a durable grouping layer that prioritizes cognitive upgrading first, then methods, then execution experience.
- `思考` - the distilled conclusion or hypothesis extracted from repeated events and themes.

## Storage Rules

- KeyPulse remains the raw capture layer.
- Obsidian remains the knowledge surface.
- Generated notes stay local and file-based.
- Sensitive sources continue to be denied or redacted before export.

## Sink Discovery

The install flow and runtime export flow must resolve a primary sink automatically.

Resolution order:

1. An explicit CLI override.
2. A persisted sink binding from the last successful discovery.
3. A detected Obsidian vault on the local machine.
4. The configured standalone markdown vault path.

Supported v1 sinks:

- Obsidian vaults detected through local vault markers or the Obsidian app config.
- Standalone markdown folders that use the same note layout without requiring a running app.

Discovery must be non-fatal. If no sink is found, KeyPulse still runs and exports into the standalone vault path.

## Vault Layout

- `Daily/` for daily rollups
- `Events/` for atomic event cards
- `Topics/` for theme cards
- `Inbox/` for raw imports pending review
- `Sources/` for clipped references
- `Archive/` for retired notes

## Export Contract

- `keypulse export --format obsidian --output <vault-path>` writes markdown notes.
- `keypulse obsidian sync` is the scheduled daily entrypoint.
- Exported notes include YAML frontmatter and deterministic file paths.
- Event cards link back to topic cards.
- Daily notes link to all generated cards for that date.
- `keypulse sinks detect` may be used by the installer or a user to refresh the active sink binding.

## Theme Priority

When events are promoted into themes, the grouping order is:

1. Cognitive upgrade
2. Methodology
3. Execution experience

## Operating Loop

1. KeyPulse captures activity.
2. The sink resolver selects the best local sink or falls back to standalone mode.
3. The exporter converts selected activity into event cards.
4. Repeated events are grouped into topic cards.
5. Daily notes summarize the day.
6. Daily sync runs at `09:05` and exports yesterday's data.
7. Weekly review promotes stable patterns into evergreen knowledge.
