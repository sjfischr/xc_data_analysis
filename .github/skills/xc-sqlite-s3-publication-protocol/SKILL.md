---
name: xc-sqlite-s3-publication-protocol
description: 'Use when publishing or restoring database snapshots: S3 key layout, manifest schema, writer lease, compare-and-swap sequence (If-None-Match/If-Match), and the append-only restore procedure.'
---

# S3 publication protocol

> Status: **normative architecture** (source: spec design.md §7).
> Docs checked 2026-09-20:
> [S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

## Key layout

```text
database/active.json                          # the manifest (single source of truth)
database/locks/writer.json                    # writer lease
database/snapshots/<publication-id>/xc.db     # immutable snapshots
database/reports/<publication-id>/reconciliation.json
raw/<source>/<yyyy>/<sha256>.json.gz          # immutable raw payloads
jobs/<run>/status.json                        # ingestion run status
feasibility/<run-id>/                         # Task 3 probe outputs
```

## Manifest fields (`database/active.json`)

`publication_id`, `parent_publication_id`, `snapshot_key`,
`snapshot_version_id`, `sha256`, `byte_size`, `schema_version`,
`created_at`, `created_by`, and a `summary` block
(`results`, `athletes`, `schools`, `meets` counts).

`parent_publication_id` makes history an append-only chain — every
publication (including restores) points at its predecessor.

## Writer sequence (single writer, enforced twice)

1. Acquire the **lease** (`database/locks/writer.json`: owner, run ID,
   acquisition time, expiry, heartbeat) — created with `If-None-Match: *`,
   renewed by heartbeat, replaced only with `If-Match`.
2. Download the current snapshot; verify its hash against the manifest.
3. Apply changes locally in `BEGIN IMMEDIATE`; checkpoint WAL; produce the
   publication copy via the SQLite Backup API.
4. Verify: `PRAGMA foreign_key_check`, `PRAGMA integrity_check`, compute
   SHA-256 and byte size.
5. Upload the new snapshot with `If-None-Match: *` (create-only, never
   overwrite) — **before** touching the manifest.
6. Swap the manifest with `If-Match: <prior ETag>` (compare-and-swap). On
   CAS failure: keep the prior manifest, mark the uploaded snapshot an
   orphan candidate for cleanup, and fail the run loudly.

The lease prevents concurrent writers; the manifest CAS catches any race the
lease misses. Both are required.

## Restore procedure (append-only, auditable)

Restoring to a previous state publishes a **new** `active.json` whose
`snapshot_key` points at the old immutable snapshot and whose
`parent_publication_id` is the current publication. Nothing is deleted or
rewritten; readers converge on the restored snapshot at their next manifest
poll. Rollback of a bad publication is therefore just another publication.

## Anti-patterns

- Overwriting `active.json` without `If-Match` (lost-update race).
- Re-uploading over an existing snapshot key.
- "Restoring" by deleting the bad snapshot or editing history.
- Publishing a snapshot whose reconciliation report was never generated.
