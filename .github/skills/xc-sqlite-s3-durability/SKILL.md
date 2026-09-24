---
name: xc-sqlite-s3-durability
description: 'Use when reading/writing the SQLite database in relation to S3: the prohibition on writable SQLite-over-S3 mounts, immutable snapshots, the verified reader cache pattern, and mode=ro immutable=1 opens.'
---

# SQLite-on-S3 durability pattern

> Status: **normative architecture** (source: spec design.md §7).
> Docs checked 2026-09-20: [SQLite URI parameters](https://www.sqlite.org/uri.html),
> [SQLite Backup API](https://www.sqlite.org/backup.html),
> [How to corrupt an SQLite database](https://www.sqlite.org/howtocorrupt.html).

## The prohibition

**Never open a writable SQLite database over an S3 mount** (s3fs,
mountpoint-s3, rclone mount, or similar). S3 does not provide the POSIX
locking and page-level atomicity SQLite's journal/WAL machinery requires;
this corrupts databases under any concurrency. This applies to WAL mode too.

## The pattern we use instead

1. **Writes happen locally.** The publication writer downloads the current
   snapshot to local disk, applies changes there inside `BEGIN IMMEDIATE`,
   and verifies (`PRAGMA foreign_key_check`, `PRAGMA integrity_check`).
2. **Snapshots are immutable.** Each publication uploads a brand-new object
   (`database/snapshots/<publication-id>/xc.db`), created with the SQLite
   **Backup API** after a WAL checkpoint — never a live db file copied
   mid-write. S3 Versioning stays enabled as a safety net.
3. **Readers never touch S3 directly through SQLite.** They download the
   snapshot referenced by the active manifest, verify byte size + SHA-256 +
   schema version + `PRAGMA quick_check`, atomically rename into a local
   cache keyed by publication ID, then open with
   `file:xc.db?mode=ro&immutable=1`.
4. **One publication per request.** A request pins its publication ID so all
   its queries see one consistent database.
5. **No streaming replication in release 1** (no Litestream); publication
   frequency is low enough that snapshot-per-publication is simpler and
   cheaper.

## Reader cache sketch

```text
GET database/active.json (short TTL, keep ETag)
  → cache hit on publication_id? open cached file
  → else download snapshot → verify size/sha256/schema/quick_check
    → atomic rename into cache → open mode=ro&immutable=1
```

## Anti-patterns

- Any FUSE/S3 mount under a writable SQLite file — including "just for one
  quick migration".
- Opening a snapshot without verifying its hash against the manifest.
- Editing a published snapshot object in place (breaks immutability and every
  cached reader).
- Cross-request reuse of a connection without re-pinning the publication.
