# story: packaging-validation [cli]
As a release operator I can run packaging validation so that the published artifact has coherent package metadata and runtime behavior.

## Rules
- Packaging validation runs from the terminal as part of release readiness.
- The package under validation reports the expected release version `0.5.1`.
- Packaging failures are release-blocking and visible to the operator.

## Examples
- Given the `0.5.1` release is being prepared
  When I run the packaging validation checks
  Then the checks pass and confirm the package version expected for this release

- Given packaging validation reports a failure
  When release readiness is evaluated
  Then the release is not considered ready until the failure is fixed
