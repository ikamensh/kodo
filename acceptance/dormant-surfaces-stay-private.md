# story: dormant-surfaces-stay-private [cli]
As a user installing Kodo I only see product surfaces that are ready to ship so that dormant experiments are not mistaken for release promises.

## Rules
- `kodo/knowledge` remains dormant and is not exposed as a package entry point.
- No `kodo knowledge` CLI command is added in this iteration.
- No `kodo doctor` CLI command is added in this iteration.
- Release hygiene changes do not expand the product surface beyond the current iteration scope.

## Examples
- Given I install or inspect the release package
  When I list available Kodo command surfaces
  Then `kodo knowledge` is not offered as a shipped command

- Given issue `#53` tracks a future `kodo doctor` direction
  When this release iteration is validated
  Then no `kodo doctor` command has been added as part of release hygiene
