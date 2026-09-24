"""SQLite/S3 publication: manifests, verified reading, and single-writer
publishing (Task 6, design.md section 7).

The feasibility probe in :mod:`xc_platform.feasibility.s3_protocol` proved
the protocol this package implements (create-only immutable uploads,
manifest compare-and-swap, a conditional writer lease) against real S3 using
the AWS CLI, deliberately without a production dependency (Task 3.4). This
package is the production implementation: it uses boto3 (pinned in Task 6)
behind a narrow :class:`~xc_platform.db.publication.s3_client.S3Client`
interface, so the publication logic itself can be tested fast and
deterministically against
:class:`~xc_platform.db.publication.s3_client.FakeS3Client` without a
network call, while a smaller real-S3 suite (Task 6.5) confirms the fake is
faithful to actual S3 conditional-write semantics.

* :mod:`xc_platform.db.publication.models` -- ``Manifest`` and
  ``WriterLease``, validated at construction (Task 6.1).
* :mod:`xc_platform.db.publication.reader` -- ``SnapshotReader``, the
  verified read-only snapshot manager (Task 6.2).
* :mod:`xc_platform.db.publication.writer` -- ``SnapshotPublisher``, the
  single-writer publisher and restore operation (Task 6.3, 6.4).
"""

from __future__ import annotations
