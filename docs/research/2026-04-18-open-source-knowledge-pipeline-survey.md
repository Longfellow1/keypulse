# Open Source Knowledge Pipeline Survey

Date: 2026-04-18

## Goal

Evaluate a small set of open source knowledge and memory projects as reference points for KeyPulse, with a clear answer on what can be reused, what should only inform the framework, and what should not be adopted as the product core.

This survey uses a strict standard:

- "directly usable" means KeyPulse could adopt it now as a sidecar or optional backend without changing product identity
- "framework reference" means the ideas are valuable but the project shape does not match KeyPulse
- "not recommended directly" means the gap is large enough that importing it would create more product drift than leverage

## Evaluation Criteria

- Stack fit with KeyPulse's current Python/macOS codebase.
- Product fit with a capture-first, privacy-first activity memory app.
- Coverage of the full chain: capture, normalize, redact, store, export, route, and retrieve.
- Operational maturity: local-first defaults, deterministic behavior, and clear contracts.
- Reuse risk: how much of the upstream product shape KeyPulse would need to absorb.

## Project-by-Project Conclusions

| Project | Stack / Shape | Conclusion | Relation to KeyPulse |
|---|---|---|---|
| [Knolo Core](https://github.com/HiveForensics-AI/knolo-core) | TypeScript / pack-based retrieval engine | Not a drop-in fit. Strong reference for deterministic retrieval and portable artifacts, but the pack model is not KeyPulse's product shape. | Borrow the idea of versioned, portable output bundles if KeyPulse later needs a structured export or retrieval artifact. |
| [kbx](https://github.com/tenfourty/kbx) | Python / local knowledge base with FTS5 + vector search | Closest technical neighbor, but still not a product skeleton for KeyPulse. Good framework reference for hybrid search, MCP exposure, and CLI ergonomics. | Best candidate for a future optional search backend or search-layer inspiration. Not a reason to rebuild KeyPulse around notes-first indexing. |
| [NeuroStack](https://github.com/raphasouthall/neurostack) | Node / memory layer over notes | Framework reference only. Strong ideas around stale-note detection, session memory, and read-only vault behavior. | Useful for recall quality, temporal decay, and note freshness heuristics. Not a capture pipeline. |
| [Atomic](https://github.com/kenforthewin/atomic) | Rust / desktop + server + graph UI | Not recommended as a direct base. It is broader, heavier, and centered on graph/workspace UX rather than activity capture. | Good for graph visualization and multi-client architecture ideas, but too far from KeyPulse's core. |
| [SwarmVault](https://github.com/swarmclawai/swarmvault) | Node / knowledge compiler and graph toolchain | Framework reference only. Strong at source sessions, review bundles, and multi-input compilation, but its workflow is document-compilation centric. | Useful for staged transforms, reviewable outputs, and schema-guided exports. Not a KeyPulse core. |
| [GNO](https://github.com/gmickel/gno) | Bun / local search, retrieval, synthesis | Framework reference only. Strong hybrid retrieval and publishing model, but it is a general document workspace, not a capture daemon. | Useful for search routing, explainability, and export-as-artifact ideas. |
| [kb-wiki](https://github.com/samstill/kb-wiki) | Node / minimal LLM Wiki implementation | Framework reference only. It is a thin LLM Wiki wrapper and useful mainly as a layout and folder-convention example. | Good for lightweight wiki surfaces and subagent scaffolding, but too thin to define KeyPulse's pipeline. |
| `/Users/Harland/Go/data_analysis/migration_package` | Local Python analysis pipeline | Directly useful as a process reference. It already shows a clean split between raw inputs, analysis pipeline, report generation, and docs. | Good local template for staged pipeline docs, bridge separation, and output discipline. |

## Grouped Conclusions

### Directly Usable Today As Sidecars Or Backend Candidates

- [kbx](https://github.com/tenfourty/kbx)
- [GNO](https://github.com/gmickel/gno)

These are the only surveyed projects that are close enough to KeyPulse's retrieval and local-ops needs to justify direct experimentation as optional sidecars. Even here, "directly usable" does not mean "replace KeyPulse with this project." It means KeyPulse could later add an adapter to call into a KBX- or GNO-like retrieval service behind a backend interface.

### Directly Usable As Local Process References

- `/Users/Harland/Go/data_analysis/migration_package`

This is not an upstream dependency, but it is directly useful as an internal design reference because it already follows the staged pattern KeyPulse needs: deterministic analysis first, targeted LLM calls second, and structured output generation at the end.

### Borrow The Framework

- [NeuroStack](https://github.com/raphasouthall/neurostack)
- [SwarmVault](https://github.com/swarmclawai/swarmvault)
- [kb-wiki](https://github.com/samstill/kb-wiki)
- [Knolo Core](https://github.com/HiveForensics-AI/knolo-core)

These projects are better treated as design sources:

- NeuroStack for stale-note detection, session memory, and tiered retrieval
- SwarmVault for raw/wiki separation, review bundles, and schema-guided compilation
- kb-wiki for the simplest raw/wiki/project layout
- Knolo for lexical-first retrieval and deterministic artifact thinking

### Do Not Adopt Directly

- [Atomic](https://github.com/kenforthewin/atomic)

Atomic is a strong product, but it is too far toward graph workspace UX and cross-client knowledge authoring to serve as KeyPulse's product base. Pulling it in would move the project away from activity capture and privacy-boundary control.

## Final Recommendation

No surveyed project should become the KeyPulse product shell.

KeyPulse should stay a capture-first, privacy-first product. The right move is to keep its own capture, privacy, export, and routing pipeline, then borrow only the useful pieces:

- from kbx: hybrid retrieval and CLI/search ergonomics
- from NeuroStack: stale-freshness and recall quality ideas
- from SwarmVault and GNO: reviewable exports and explainable retrieval
- from Knolo: versioned artifact thinking, if KeyPulse ever needs it
- from `migration_package`: staged pipeline structure, prompt discipline, and regenerate/review loops

The practical path is:

1. keep KeyPulse's own capture, privacy, pipeline, and sink layers
2. define a clean retrieval backend interface
3. keep the default retriever local and deterministic
4. evaluate KBX-like or GNO-like retrieval only behind that interface

Selective borrowing is the right move. Wholesale adoption is not.
