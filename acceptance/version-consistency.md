# story: version-consistency [cli]
As a release operator I can confirm the installed package version and runtime version agree so that a release does not ship with conflicting identity.

## Rules
- The package metadata version and runtime `kodo.__version__` report the same release version.
- The expected release version for this iteration is `0.5.1`.
- A mismatch is treated as release-blocking rather than silently tolerated.

## Examples
- Given the project is prepared for release `0.5.1`
  When I inspect package metadata and the runtime version from the terminal
  Then both report `0.5.1`

- Given package metadata reports `0.5.1`
  When the runtime reports any other version
  Then release validation fails with the mismatch visible to the operator
