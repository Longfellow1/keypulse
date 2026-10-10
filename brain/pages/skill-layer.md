---
id: skill-layer
title: "Skill layer (propose, evaluate, evolve): planned, not implemented"
category: project
status: active
created: "2026-10-10T10:26:13"
updated: "2026-10-10T10:26:13"
---

<!-- compiled_truth -->
## Goal
Let the user name and curate skills or principles. The LLM proposes candidates, code records them (see [[user-curates-llm-proposes]]). Principle distillation L7 is the nearest existing piece (`pipeline/principle_distillation.py`, `obsidian/principle_exporter.py`).

## Status
- Not implemented: `keypulse eval skill` and `keypulse skill propose` print "placeholder" (`keypulse/cli.py` ~L3398).
- No Gold Set baseline yet; CLAUDE.md requires one before shipping (see [[gold-set-gated-prompt-changes]]).
- Evolution plan doc exists; execution status unknown.

## Open
Order of work: skill v0 first, then the evolution layer. TODO confirm.


## Timeline

- time: 2026-10-10T10:26:13
  kind: decision
  summary: "Created this page: Skill layer (propose, evaluate, evolve): planned, not implemented"
  source: "docs/设计-skill-v0-plan.md; docs/设计-openspace-inspired-keypulse-evolution-plan.md; cli.py eval skill and skill propose placeholders"
  affects: [skill-layer]

- time: 2026-10-10T10:26:13
  kind: decision
  summary: captured from docs and code
  source: "docs/设计-skill-v0-plan.md; docs/设计-openspace-inspired-keypulse-evolution-plan.md; cli.py eval skill and skill propose placeholders"
  affects: [skill-layer]
