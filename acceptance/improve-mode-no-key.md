# story: improve-mode-no-key [cli]
As a maintainer I can run improve mode with mocked dependencies so that the project-improvement workflow remains verifiable without credentials.

## Rules
- Improve mode runs in a mocked no-key path.
- The workflow detects whether the target project behaves like an application or library.
- The orchestrator receives a staged improvement plan rather than an empty or ad hoc task.

## Examples
- Given no provider API keys are configured
  When I run the documented mocked improve workflow
  Then Kodo builds an improve plan and starts orchestration without requiring external services

- Given improve mode analyzes a project
  When the plan is handed to the orchestrator
  Then the plan includes the expected staged improvement flow for the detected project type
