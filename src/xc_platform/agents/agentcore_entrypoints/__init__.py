"""AgentCore Runtime entrypoints (Task 11.5, design.md section 12.6).

Everything under this package is written to the platformVersion=V2
startup/per-request split (design.md section 12.6's table): only
snapshot-safe values (imports, model weights, static config, the pinned
SQLite snapshot, warmed-up clients) are computed at module scope, inside
each entrypoint module; randomness, time, credentials, and per-request
identity are computed inside ``@app.entrypoint`` handlers.

Module-scope client construction (``Boto3S3Client.create``,
``BedrockModel``) is lazy -- credentials are resolved on an actual AWS API
call, not at construction -- so each module here is safely importable in
ordinary CI (with ``XC_SNAPSHOT_BUCKET`` set for the analytics entrypoint;
see its test file) and its per-request helper functions are directly unit
tested. What is NOT covered by ordinary tests is the actual snapshot
open and model invocation -- those need a real published snapshot and a
real Bedrock call, and are exercised instead through the fully mocked
:mod:`xc_platform.agents.runtime`/``analytics_agent``/``resolution_agent``
tests, or, for the deployed contract end to end (``/ping``, streaming,
session/actor isolation, cold start), only via a real ``agentcore deploy``
-- exactly how the Task 3.5 feasibility gate verified this layer
(docs/agentcore-deployment-evidence.md), not through a test suite.
"""
