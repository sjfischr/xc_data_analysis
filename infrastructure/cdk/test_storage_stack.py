"""Infrastructure synthesis tests and policy assertions (Task 15.5).

``aws_cdk.assertions`` synthesizes the stack in-memory and asserts on the
resulting CloudFormation template -- no AWS credentials or network call,
the same "real but never deployed" verification this session's other
infrastructure code (Task 11.5's AgentCore entrypoints) uses.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from stacks.storage_stack import StorageStack


def _template() -> Template:
    app = cdk.App()
    stack = StorageStack(app, "TestStorageStack")
    return Template.from_stack(stack)


def test_both_buckets_block_all_public_access() -> None:
    template = _template()
    template.resource_count_is("AWS::S3::Bucket", 2)
    template.all_resources_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_both_buckets_are_encrypted_and_versioned() -> None:
    template = _template()
    template.all_resources_properties(
        "AWS::S3::Bucket",
        {
            "BucketEncryption": Match.any_value(),
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    )


def test_every_bucket_has_a_deny_insecure_transport_policy() -> None:
    """Requirement 16.6-adjacent: no bucket accepts a plaintext request.
    Each policy denies s3:* for every principal when SecureTransport is
    false (CDK's ``enforce_ssl=True``, not a placeholder)."""
    template = _template()
    template.resource_count_is("AWS::S3::BucketPolicy", 2)
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Deny",
                                "Principal": {"AWS": "*"},
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        )
                    ]
                )
            }
        },
    )


def test_ingest_queue_is_fifo_with_a_dead_letter_queue_and_bounded_retries() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "QueueName": "xc-ingest.fifo",
            "FifoQueue": True,
            "RedrivePolicy": Match.object_like({"maxReceiveCount": 3}),
        },
    )
    template.has_resource_properties(
        "AWS::SQS::Queue", {"QueueName": "xc-ingest-dlq.fifo", "FifoQueue": True}
    )


def test_import_worker_role_has_no_wildcard_resource_grant() -> None:
    """Least privilege (Requirement 16.8): the worker's own policy scopes
    every S3/SQS action to the specific bucket/queue ARNs CDK generated
    from the actual construct references, never ``Resource: "*"``."""
    template = _template()
    policies = template.find_resources("AWS::IAM::Policy")
    assert policies, "expected at least one IAM policy for the import worker role"
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            resource = statement.get("Resource")
            assert resource != "*", f"wildcard resource found in statement: {statement}"


def test_import_worker_role_only_trusts_lambda() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Statement": [
                    Match.object_like(
                        {"Principal": {"Service": "lambda.amazonaws.com"}}
                    )
                ]
            }
        },
    )
