# story: no-key-cli-run [cli]
As a developer I can run Kodo in a mocked, non-interactive no-key workflow so that I can verify the core orchestrator path before publishing.

## Rules
- The supported non-interactive smoke workflow runs without external API keys.
- The CLI starts, runs one mocked orchestration cycle, and exits successfully.
- The workflow produces machine-readable run output that includes the run lifecycle.

## Examples
- Given no provider API keys are configured
  When I run the documented non-interactive mocked CLI workflow
  Then Kodo starts a run, completes one mocked cycle, and exits with status 0

- Given the mocked CLI workflow completes
  When I inspect the emitted output
  Then it includes the run start, cycle completion, and run end information
