# Artifact Layout Backlog

Recorded on 2026-07-20. Implement after the stage5 deployment-plan closure.

- Organize contexts under `ops/contexts/<context_id>/<action>/`.
- Organize artifacts under `ops/artifacts/<context_id>/<action>/`, with dedicated
  `state/`, `stages/`, and `logs/` directories.
- Retain only the latest three timestamped SDK/platform/plugin build run groups.
- Reuse archives when the input SHA-256 is unchanged.
- Keep formal release archives and their provenance hashes outside history cleanup.
- Add an explicit `cleanup-artifacts` command that prints and records deletions.
- Migrate legacy flat files without silently losing evidence referenced by the
  current status, failure, approval, package, or release reports.
