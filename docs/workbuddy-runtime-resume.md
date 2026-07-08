# WorkBuddy Runtime Resume Contract

## Goal

WorkBuddy is still a single-agent C-class host and does not provide a native
workflow DAG. The runtime gap fixed here is narrower and concrete: after a user
approves the next step, the host must not ask the LLM to describe a plan again.
It must execute a structured resume payload.

## Runtime Flow

1. WorkBuddy reaches an FDE human gate and records `gate_protocol.json`.
2. The user confirms or rejects the next step.
3. `WorkBuddyResumeAdapter.confirm_and_resume(...)` commits the StateGuard
   transition from `gate_phase`.
4. The adapter stores `resume_signal` with `control_decision`,
   `requires_dispatch`, `next_action`, and `mechanical_enforcement_status`.
5. WorkBuddy reads `next_workbuddy_payload(...)` and executes `must_execute`.

## Resume Signal

The confirmation path writes a `fde-workbuddy-resume-signal`:

```json
{
  "kind": "fde-workbuddy-resume-signal",
  "control_decision": "continue",
  "from_state": "gate_phase",
  "to_state": "context",
  "requires_dispatch": true,
  "next_action": {
    "type": "dispatch_worker",
    "agent": "research",
    "tool": "call_research_agent"
  },
  "forbidden_reply": "Do not answer with a plan or preparation note; execute next_action.",
  "mechanical_enforcement_status": "state_guard_committed"
}
```

If the current state is not `gate_phase`, the adapter writes
`control_decision: "wait_human"` and leaves the state unchanged. If the user
rejects the step, it commits `gate_phase -> aborted` and writes
`control_decision: "cancel"`.

## Host Rule

When `workbuddy_next_payload.status == "ready"`, the host must execute
`must_execute` directly. A natural-language reply such as "I will next..." or
"I am ready to..." is a runtime failure, because the confirmation has already
been converted into an executable state transition.
