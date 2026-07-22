---
name: qa-risk-panel-review
description: Run a structured multi-perspective risk review for high-impact FDE deliverables before the Lead score gate.
version: 1.0.0
---

# Risk Panel Review

Use for high-risk architecture, production rollout, security, data, or executive recommendations.

Review the artifact through four independent lenses: customer outcome, implementation feasibility, operational failure, and safety/compliance. For each finding record evidence, severity, affected acceptance criterion, mitigation, and residual risk. Do not average away a critical dissent. Return `BLOCK` if any critical risk lacks an owner and verifiable mitigation; otherwise return `PASS_WITH_ACTIONS` or `PASS`.
