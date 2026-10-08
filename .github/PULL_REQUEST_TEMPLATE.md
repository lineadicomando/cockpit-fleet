## What and why

<!-- Describe what this PR changes and the motivation behind it. -->

## How to test

<!-- Steps to verify the change on a controller with the package installed. -->

## Checklist

- [ ] `npx tsc --noEmit` passes
- [ ] `make check` passes
- [ ] `make deb` builds a package with no lintian error or warning
- [ ] `test/integration/privileges.sh` and `sandbox.sh` pass on a Debian 13 controller
- [ ] No new code runs as root, and no secret can reach argv, logs or the frontend
