# story: resume-interrupted-run [cli]
As an operator I can resume an interrupted Kodo run so that persisted work is recoverable after a stop or crash.

## Rules
- An incomplete run can be discovered from persisted run state.
- `kodo --resume` resumes the selected incomplete run instead of starting an unrelated new run.
- The resumed orchestrator receives the prior resume state needed to continue explainably.

## Examples
- Given a previous run ended before completion
  When I run Kodo with resume enabled
  Then Kodo selects the incomplete run and passes its resume state to the orchestrator

- Given a resumed run continues
  When run events are emitted
  Then the events remain associated with the resumed run rather than a fresh unrelated run
