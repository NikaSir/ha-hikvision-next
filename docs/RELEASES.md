# Update and release policy

## Current repository state

- `main` is the canonical source branch, and accepted changes reach it through reviewed pull requests after required checks pass.
- The integration version is [`1.1.9`](../custom_components/hikvision_next/manifest.json).
- At the 2026-09-14 audit baseline, this repository had no Git tags or GitHub Releases.
- The README documents installation as a custom HACS repository. End-to-end acceptance that HACS exposes and installs the current `main` state was not performed by that audit, so a merged commit must not be described as delivered until that check succeeds on the target Home Assistant installation.

## Acceptance gate

Before an update is presented to users:

1. Pytest, Hassfest and HACS validation pass for the reviewed commit.
2. Home Assistant loads the existing `hikvision_next` config entries without migration loss.
3. A real NVR or camera verifies event-source filtering, event delivery, polling failure and recovery, reload behavior and preservation of user-disabled entities.
4. The accepted commit SHA and integration version are recorded, and the previous accepted state remains available for rollback.
5. `CHANGELOG.md` describes the user-visible change.

Creating a tag or GitHub Release is a separate publication decision. This policy does not create one or claim that a release-based HACS channel is already configured.
