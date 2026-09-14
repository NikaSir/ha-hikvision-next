# Changelog

Current integration version: [`1.1.8`](custom_components/hikvision_next/manifest.json).

Earlier project history remains available in Git history and the upstream project; it is not reconstructed here.

## 1.1.8 — 2026-09-14

- Upgrade the runtime XML parser from `xmltodict==0.13.0` to `xmltodict==1.0.4`; test requirements use the same version.
- Add a check that the runtime dependencies installed in CI satisfy `manifest.json`.
- Validate on Python 3.12 and 3.14 with compatible Home Assistant test dependencies, including event parsing and exact outgoing XML payload assertions.
- This records a source update. Installation through HACS and acceptance on real Hikvision hardware remain pending.

## Repository maintenance — 2026-09-14

- Add repository ownership, weekly dependency update configuration, editor defaults and an explicit update policy.
- Keep the integration and runtime version unchanged.
