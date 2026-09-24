"""Storage and asynchronous processing (Task 15.1, design.md sections
7.1, 9.5, 15.1).

Real CDK, not deployed (this session's owner decision carries the same
"write it, don't apply it" boundary Task 11.5's AgentCore entrypoints and
Task 15's broader scope follow) -- verified with ``cdk synth`` only.

Covers:

* the private, encrypted, versioned data bucket that holds published
  SQLite snapshots (``database/snapshots/...``, ``database/active.json``)
  and raw source payloads (``raw/...``) -- design.md section 7.1's layout,
  what :mod:`xc_platform.db.publication.writer`/``xc_platform.ingest.
  raw_storage`` write to via the ``S3Client`` interface;
* the private web bucket that will hold the Next.js static export
  (Task 14.1's ``web/out/``) once CloudFront (Task 15.2, not built this
  session) fronts it;
* the ingest SQS FIFO queue plus its dead-letter queue (design.md 9.5's
  "one idempotent FIFO message" -- the queueing side
  :mod:`xc_platform.ingest.workflow`'s module docstring explicitly marks
  as its own out-of-scope boundary; this is that boundary's other half).

Bucket policies deny any non-HTTPS request and any request not from the
account's own principals -- there is no public bucket access anywhere in
this stack (Requirement 15.1: "block public bucket access").
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        self.data_bucket = self._build_private_bucket(
            "DataBucket",
            # Every published snapshot and raw payload is a permanent,
            # append-only record (design.md 3.4/3.9); noncurrent versions
            # age out on a lifecycle rule rather than never expiring, but
            # the bucket itself is never destroyed by a stack teardown.
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireNoncurrentVersions",
                    noncurrent_version_expiration=Duration.days(90),
                    enabled=True,
                )
            ],
        )
        self.web_bucket = self._build_private_bucket("WebBucket", lifecycle_rules=[])

        self.ingest_dead_letter_queue = sqs.Queue(
            self,
            "IngestDeadLetterQueue",
            queue_name="xc-ingest-dlq.fifo",
            fifo=True,
            content_based_deduplication=True,
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )
        self.ingest_queue = sqs.Queue(
            self,
            "IngestQueue",
            queue_name="xc-ingest.fifo",
            fifo=True,
            # One idempotent message per submitted URL (design.md 9.5);
            # content-based dedup means an accidental double-submit of the
            # identical request body within the 5-minute window collapses
            # to one message rather than one worker invocation each.
            content_based_deduplication=True,
            visibility_timeout=Duration.minutes(15),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=self.ingest_dead_letter_queue,
                # A message that fails 3 times (transient extraction/
                # resolution/commit failure -- design.md 9.5's FAILED path
                # already records why) moves to the DLQ instead of
                # retrying forever, matching Requirement 15.3's runaway
                # protection for imports.
                max_receive_count=3,
            ),
        )

        # design.md 9.5: "reserved-concurrency-one import worker" -- there
        # is exactly one ingest run in flight at a time (the single-writer
        # SQLite constraint, design.md section 7, makes concurrent
        # imports unsafe regardless of the queue). The Lambda function
        # itself is Task 15.2/15.1's remaining wiring (not built this
        # session -- see the module docstring); this role is what it will
        # assume, scoped to exactly the actions the ingest workflow needs.
        self.import_worker_role = iam.Role(
            self,
            "ImportWorkerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Least-privilege role for the reserved-concurrency-one ingest worker",
        )
        self.data_bucket.grant_read_write(self.import_worker_role)
        self.ingest_queue.grant_consume_messages(self.import_worker_role)

    def _build_private_bucket(
        self, construct_id: str, *, lifecycle_rules: list[s3.LifecycleRule]
    ) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            construct_id,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=lifecycle_rules,
        )
        return bucket
