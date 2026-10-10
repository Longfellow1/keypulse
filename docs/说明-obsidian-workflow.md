# KeyPulse Obsidian Workflow

KeyPulse treats Obsidian as the reading surface for local personal AI memory.

It does not replace your PKM system. It writes plain Markdown back into it, so the daily reflection becomes part of the graph you already own.

## Core loop

```text
Mac work surface
  -> local event store
  -> privacy filters before persistence
  -> daily reflection renderer
  -> Obsidian vault
  -> user review and checkbox feedback
```

## Vault contract

KeyPulse writes local Markdown files into these folders:

| Folder | Purpose |
|---|---|
| `Daily/` | Daily reflection notes and timeline summaries |
| `Events/` | Atomic evidence cards for specific work moments |
| `Topics/` | Durable themes that recur across days |
| `Anchors/` | Named patterns, methods, and recurring work surfaces |

Each generated note should remain usable without KeyPulse:

- YAML frontmatter for Obsidian Properties and Bases
- human-readable headings
- stable wikilinks between daily notes, topics, events, and anchors
- checkbox feedback that can be reviewed inside Obsidian
- no proprietary database required to read the output

## Daily review shape

A good KeyPulse daily note should answer:

1. What did I keep returning to?
2. What did I manually write, copy, or save?
3. Which work blocks looked meaningful enough to revisit?
4. Which topic or anchor is getting stronger?
5. What should I inspect or name tomorrow?

The note should not become:

- a productivity score
- a screen-time leaderboard
- a surveillance transcript
- a generic AI-generated journal entry

## Recommended Obsidian setup

Minimal setup:

- keep generated files under a `KeyPulse/` folder or a dedicated vault
- pin `Daily/` in bookmarks
- review yesterday's note once each morning
- use checkbox feedback instead of editing generated evidence directly

Optional setup:

- use Obsidian Properties to filter `source: keypulse`
- use Bases or Dataview to list open review items
- use tags such as `#keypulse`, `#daily-review`, `#work-pattern`
- promote repeated observations into hand-written evergreen notes

## Feedback loop

KeyPulse works best when Obsidian stays the place where the user decides what matters.

Recommended review actions:

```md
- [ ] Confirm this pattern
- [ ] Reject this interpretation
- [ ] Split into a separate topic
- [ ] Promote to an anchor
```

The system should learn from these decisions, but the graph remains user-owned.

## Community positioning

When sharing KeyPulse with Obsidian users, describe it as a workflow first:

> Mac work traces -> local privacy filters -> Obsidian-native daily reflection notes.

Avoid presenting it as a generic AI assistant. The value for Obsidian users is that their daily notes, event cards, topic cards, and anchors stay as plain Markdown inside their own vault.
