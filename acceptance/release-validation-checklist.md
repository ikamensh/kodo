# story: release-validation-checklist [cli]
As a release operator I can follow a lightweight release validation checklist so that publishing decisions use repeatable checks instead of memory.

## Rules
- The checklist covers version consistency.
- The checklist covers supported mocked smoke workflows that can run without API keys.
- The checklist covers targeted pytest slices for CLI smoke, packaging, and touched behavior.
- Any supported workflow skipped during validation has an explicit operator-visible justification.

## Examples
- Given the release is being validated
  When I open the release validation checklist
  Then I can see the version, mocked smoke, and targeted pytest checks required for this iteration

- Given a supported mocked workflow cannot be run
  When release validation is recorded
  Then the skipped workflow and reason are captured before the release is considered ready
