# KeyPulse Architecture Decision

Date: 2026-04-18

## Status

Accepted.

## Problem Background

KeyPulse is already defined as a local-first activity memory product: it captures what happens on the machine, protects privacy at the boundary, stores events locally, and exports useful knowledge into local sinks. The open source survey confirms there are strong reference projects, but they do not match KeyPulse's product shape closely enough to become the core.

KBX is the closest neighbor on paper because it is Python-based and local-first, but it is still a note-centric knowledge base, not a capture daemon. Its architecture assumes markdown collections, retrieval-oriented indexing, and a knowledge-base workflow. KeyPulse is different: it starts with activity capture, privacy enforcement, and export routing.

## Decision

KeyPulse will continue to own its own product and its own core layers:

- capture
- privacy
- export
- sink discovery and routing
- model routing
- pipeline orchestration

KeyPulse will not be rebuilt as KBX, and it will not be renamed or republished as a KBX-based product. KBX is a reference object and a possible future optional search backend, not the product skeleton.

## Target Architecture

The target architecture is a layered system with narrow interfaces:

1. Capture core
2. Privacy boundary
3. Local event store and derived indexes
4. Information pipeline orchestrator
5. Model gateway and provider profiles
6. Export and sink adapters
7. Query and retrieval adapters

In this design, KBX can only ever sit in layer 7 as an optional retrieval backend or sidecar. It does not belong in layers 1 through 6, which are the parts that define what KeyPulse is.

## Why Not Rebuild As KBX

- KBX is optimized for notes and document retrieval, while KeyPulse is optimized for machine activity capture and privacy-preserving recall.
- Rebuilding around KBX would pull the product toward markdown vault assumptions and away from raw capture semantics.
- KeyPulse needs tight control over privacy rules, redaction, and export boundaries before any retrieval layer runs.
- The search layer is only one slice of the product. The capture and routing layers define the product identity.
- A KBX-centered rewrite would increase integration risk without solving the parts KeyPulse must own.

## What KeyPulse Must Own

- Capture watchers for windows, clipboard, idle state, and manual notes.
- Privacy policy enforcement, pattern redaction, and app-level denial rules.
- Local storage and derived indexes.
- Pipeline stages for record, write, mine, aggregate, surface, and feedback.
- Export formatting for JSON, Markdown, Obsidian, and future sinks.
- Model and provider routing for any AI-assisted pipeline stages, including local-first and cloud-capable profiles.
- The public CLI contract and the operational lifecycle of the daemon.

## What Can Stay Pluggable

- Search backends: current FTS5 path, future hybrid search, and optional external retrieval backends.
- Embedding or rerank providers.
- Export sinks: Obsidian vaults, standalone markdown, and future local sinks.
- Source adapters: browser, clipboard, window, manual input, and future capture sources.
- Artifact formats for future knowledge exports, if they preserve deterministic local output.

KBX fits here as one possible future retrieval backend reference, not as the system shell.

## Model Routing Position

KeyPulse must support both local and cloud models, but that does not change the core architecture. Model routing belongs behind a provider gateway with switchable profiles such as:

- `local-first`
- `cloud-first`
- `auto`
- `privacy-locked`

That gateway serves the pipeline stages that need models. It should not leak model-specific assumptions into capture, privacy, or sink logic.

## Open Source Progression

To move toward a cleaner open source shape without losing product control:

1. Publish narrow interfaces for capture, privacy, export, and routing.
2. Keep the core deterministic and local-only by default.
3. Document adapter contracts before adding new backends.
4. Treat search backends as interchangeable modules, not the app identity.
5. Keep product docs and architecture notes explicit about what KeyPulse is and is not.

This keeps the repo understandable to contributors while preventing accidental scope drift into a generic knowledge base.

## How To Open Source Without Turning Into KBX

The correct move is not to fork KBX and ship a renamed variant. That would create a confused story because contributors would see a notes-first retrieval engine wearing a capture-daemon name.

The better open source path is:

- keep `keypulse` as the top-level product and daemon
- expose adapter contracts so external contributors can add sinks, retrievers, and model providers
- split stable subsystems into smaller packages only after the interfaces stop moving

If package extraction becomes worthwhile later, the likely split is:

- `keypulse-capture`
- `keypulse-pipeline`
- `keypulse-model-gateway`
- `keypulse-sinks`
- `keypulse-retrieval-adapters`

## Phase Route

### Phase 1: Stabilize The Current Product

- Finish the capture, privacy, export, and sink routing story.
- Keep the daemon and local store behavior predictable.
- Keep docs aligned with actual behavior.

### Phase 2: Codify Interfaces

- Define clear boundaries between capture, normalization, privacy, storage, export, and query layers.
- Make search and sink layers explicit adapters.
- Document the lifecycle and data-flow contracts.

### Phase 3: Add Optional Backends

- Evaluate KBX-like retrieval ideas behind a backend interface.
- Allow alternative search or recall engines without changing the product shell.
- Keep the default path local, deterministic, and privacy-first.

### Phase 4: Broader Open Source Release Discipline

- Publish more docs, diagrams, and fixtures for the core pipeline.
- Keep benchmark data and architecture decisions versioned.
- Avoid renaming the product around upstream reference projects.

## Bottom Line

KeyPulse should remain KeyPulse. KBX is useful as a reference and possibly a future search backend, but it is not the foundation for the product identity, and it should not become the renamed core of the project.
