# Unreleased

This repository contains an experimental source snapshot, not a verified
versioned release. No v0.1.0 tag or GitHub prerelease exists.

Local tests and targeted scans passed. The connected GitHub integration could
create the public repository and commit source files, but GitHub rejected the
`.github/workflows/tests.yml` upload. Its current authorization does not
include workflow-file write permission, so remote CI and the final release
gates could not run.

To complete the release, reconnect the GitHub integration in Replit and grant
the workflow permission needed to add the reviewed read-only CI file. Then run
CI on the exact commit, independently clone the public repository, repeat the
demo, and only after those checks pass create the v0.1.0 annotated tag and
GitHub prerelease. Until then, do not describe this repository as a verified
release.
