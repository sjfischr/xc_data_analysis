# SQLite/S3 publication protocol evidence (Task 3.4, gate F6)

**Observed:** September 20, 2026, against real S3 in `us-east-1`,
account `918221680168`. **Evidence:**
`feasibility/runs/<run-id>-s3-protocol/`. **Verdict: F6 PASS, 14/14 checks.**

The probe created a disposable bucket, ran every scenario, and deleted the
bucket plus all 9 object versions in a `finally` block. A follow-up
`list-buckets` confirmed no `xc-feasibility*` bucket remains.

S3 access went through the AWS CLI (2.36.49) rather than boto3 deliberately:
the feasibility phase must not add production Python dependencies before the
Task 3.8 gate. Task 6 pins boto3 for the real implementation.

## Bucket posture (verified, not assumed)

Each setting was written and then read back:

| Setting | Value |
|---|---|
| Versioning | `Enabled` |
| Default encryption | `AES256` with bucket keys |
| Public access block | all four flags true |
| Object ownership | `BucketOwnerEnforced` |

## Measured performance

Representative snapshot: **1.309 MiB**, 4,204 rows, built from the frozen
baseline CSV. (Not the production schema — that is Task 4 — just a real SQLite
file of representative size so the timings mean something.)

| Operation | Measured |
|---|---|
| Snapshot upload | 0.829 s |
| Manifest put | 0.593 s |
| Manifest fetch (reader) | 0.594 s |
| Snapshot download | 0.656 s (1.99 MiB/s) |
| Verify: SHA-256 + `integrity_check` + `foreign_key_check` | **0.016 s** |
| Restore via manifest CAS | 0.609 s |

**Recovery objectives supported by these numbers:** a cold reader reaches a
verified database in roughly 1.3 s (manifest + snapshot + verify), and a
restore to any prior generation completes in about 0.6 s. Both are far inside
any reasonable RTO for this workload. Verification is essentially free at this
data size, so there is no argument for skipping it.

These timings are from a single developer machine over a home connection.
Lambda-to-S3 in-region will differ; re-measure during Task 16.4.

## Single-writer enforcement (proven twice, as designed)

**Lease race.** Two genuinely concurrent threads attempted
`put-object --if-none-match "*"` on `database/locks/writer.json`.
Exactly **1 of 2** succeeded; the loser received `PreconditionFailed`.

**Manifest compare-and-swap.** Two writers read the *same* manifest ETag, each
uploaded its own snapshot, then each attempted the swap with that shared ETag.
Exactly **1 of 2** succeeded. The lost update was prevented **by the CAS alone**
— this is the correctness boundary that holds even if the lease mechanism
fails entirely.

**Lease takeover.** Replacing an existing lease with a wrong `If-Match` ETag was
rejected; replacing it with the correct ETag succeeded. An expired lease can
therefore be recovered, but only through a conditional write — never blindly.

## Failure injection: the active generation always survived

| Injected failure | Outcome |
|---|---|
| Crash before snapshot upload | Nothing written; active generation untouched |
| Crash after upload, before manifest swap | Orphan snapshot exists; active generation untouched |
| Manifest swap with a stale ETag | Rejected (`PreconditionFailed`); active generation untouched |

The active publication ID was identical before and after the whole injection
sequence, and the reader re-downloaded and fully verified that generation
afterwards. **No reader ever observed a partial or corrupt database.**

## Orphan accounting

Four snapshots existed at the end; two were referenced by a manifest version
and two were orphans — exactly the two uploaded by writers that lost their
swap. The set matched the prediction exactly.

Orphans are harmless: no manifest references them, so no reader can reach them.
They are removed by an S3 lifecycle rule. **Cleanup must never delete a
referenced generation**, and identifying orphans requires walking manifest
*versions*, not just the current manifest.

## Corruption detection — and why the checksum is not optional

Sixty-four bytes were zeroed in the middle of a downloaded snapshot:

| Check | Result on the corrupted file |
|---|---|
| SHA-256 match | **false** — corruption caught here |
| `PRAGMA integrity_check` | `ok` — **did not detect it** |

**This is the finding that matters most from this probe.** SQLite's
`integrity_check` validates b-tree structure; it happily passed a file whose
bytes had been altered. Had the reader relied on `integrity_check` alone, it
would have activated a corrupted database.

The design (§7.2) already requires size, SHA-256, schema version, *and*
integrity checks. This probe proves that ordering is not belt-and-braces
redundancy: **the SHA-256 is the load-bearing check**, and verification must
fail closed if the digest does not match, regardless of what SQLite says.

## Restore is append-only

Restore published a **new** manifest whose `snapshot_key` pointed at the
original generation's snapshot and whose `parent_publication_id` was the
publication being replaced. The old snapshot was not modified, nothing was
deleted, and the restored snapshot downloaded and verified cleanly. S3
versioning retained the full manifest history, so the publication chain is
auditable end to end.

## Residual risks not covered by this probe

1. **Scale.** 1.3 MiB is today's data. Upload/download time grows with the
   database; re-measure before assuming the timings hold.
2. **Lambda `/tmp` limits.** The reader algorithm downloads to local disk. The
   Lambda `/tmp` allocation must exceed the snapshot size with headroom for the
   previous verified snapshot retained during refresh.
3. **Cross-region/latency.** Measured from a developer workstation, not from
   the deployed runtime.
4. **Lifecycle rules were not exercised.** Orphan identification is proven;
   automated expiry is a Task 15.1 infrastructure concern.

## Task 6: the production implementation confirms these findings

**Observed:** September 22, 2026, same account, real disposable buckets.
**Code:** `src/xc_platform/db/publication/` (`models.py`, `s3_client.py`,
`reader.py`, `writer.py`). **Evidence:**
[tests/integration/test_publication_real_s3.py](../tests/integration/test_publication_real_s3.py)
(marked `live`; run explicitly, never in CI) plus 39 fast, deterministic
tests against an in-memory fake S3 with the same conditional-write
semantics (`FakeS3Client`) for every concurrency and failure path this
probe covered by hand.

boto3 1.43.99 is now pinned (`requirements.in`/`requirements.lock`,
reviewed per `docs/dependency-review.md` §3, escalated per its "AWS SDK
bringing its own network client" clause). `Boto3S3Client` is the only
module that imports it.

**3/3 real-S3 tests passed** in under 9 seconds, exercising the actual
production classes (not a CLI reproduction) end to end:

| Scenario | Result |
|---|---|
| Bootstrap publish -> read -> incremental publish -> read -> restore -> read | All manifests verified, correct lineage, restored manifest points at the original generation's unedited snapshot key |
| Two writers, one holds the lease | Contender raised `WriterBusyError` naming the holder; holder's publish would have succeeded |
| Lease with a 1s TTL, holder never releases it | Successor took over after expiry and published successfully; no manual intervention |

Buckets were created and torn down by the test fixture itself; a
post-run `list-buckets` confirmed zero `xc-publication-live-test-*`
buckets remained.

### A real defect found only by attempting real concurrency

Building the second (real-S3) test surfaced a genuine bug in `_activate()`
that the fake-S3 unit tests alone had not caught: the compare-and-swap
logic checked "no active.json exists, but a parent was given" (raise) yet
never checked the *opposite* mismatch -- "active.json already exists, but
this publish claimed no parent" -- meaning a caller could accidentally
publish a false first generation on top of real lineage and the write
would silently succeed. Fixed in `writer.py`'s `_activate()` before the
real-S3 test run, with a fast regression test
(`test_publish_without_a_parent_is_refused_once_active_json_already_exists`)
added to the deterministic suite so it never needs a real S3 call to catch
again.

### Path-traversal defense revised for restore

The original design for Task 6.1's "reject path traversal" was to require
`manifest.snapshot_key == layout.snapshot_key(manifest.publication_id)`
exactly. Building restore (Task 6.4) against design.md section 7.4's own
requirement -- "publishes a new active.json version whose snapshot
reference points to the selected [*older*] immutable object" -- showed
that exact-match check would reject every legitimate restore manifest,
since a restored manifest's snapshot necessarily belongs to a *different*
publication ID than its own. Fixed before writing any restore code: the
check is now a pattern match (`database/snapshots/<uuid>/xc.db`) that
still fully defeats a `../` traversal or an arbitrary key, without
assuming a manifest only ever references its own snapshot.
