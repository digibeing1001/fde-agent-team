---
name: echo-requirements-traceability
description: Build an evidence-backed chain from stakeholder language to FDE requirements, acceptance criteria, and unresolved assumptions.
version: 1.0.0
---

# Requirements Traceability

Use this skill when an FDE engagement needs a defensible requirements baseline.

1. Capture each stakeholder statement with source, date, confidence, and exact business context.
2. Translate it into a testable requirement without silently widening the scope.
3. Link every requirement to one success metric, one verification method, and an owner.
4. Label inferred content as an assumption and route conflicts back to `fde-lead`.
5. Report missing evidence explicitly; never manufacture stakeholder agreement.

Output a compact trace table with: `source_ref`, `need`, `requirement`, `acceptance_test`, `owner`, `confidence`, `open_question`.
