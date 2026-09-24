"""Task 3.4 -- SQLite/S3 publication protocol proof against real S3.

Verifies gate F6 from design §17 using a **disposable** versioned, encrypted,
public-access-blocked bucket that the probe creates and deletes.

What is proven here (design §7.3):

* immutable snapshot upload with ``If-None-Match: *`` (create-only);
* active-manifest compare-and-swap with ``If-Match: <etag>``;
* a writer lease that two concurrent writers cannot both hold;
* failure injection before upload, after upload, and during the manifest swap,
  each leaving the previously active generation intact;
* lease expiry and recovery, and orphan-snapshot cleanup;
* measured snapshot size, publish, download, verify, and restore timings.

S3 access goes through the AWS CLI rather than boto3 on purpose: the feasibility
phase must not add production Python dependencies before the Task 3.8 gate.
Task 6 pins boto3 for the real implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Norton intercepts TLS on the development machine; the AWS CLI ships its own
# certificate store and needs to be pointed at a bundle that includes the
# interception root. Harmless when the variable is already set or unnecessary.
_DEFAULT_CA_BUNDLE = r"C:\ProgramData\Norton\Antivirus\wscert.pem"


class S3ProbeError(RuntimeError):
    """Raised when a probe step fails in a way that invalidates the run."""


@dataclass
class CliResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self) -> Any:
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError:
            return None

    def error_code(self) -> str | None:
        """Extract the S3 error code from a failed CLI invocation."""
        for token in (
            "PreconditionFailed",
            "ConditionalRequestConflict",
            "NoSuchKey",
            "NoSuchBucket",
            "AccessDenied",
            "412",
            "409",
        ):
            if token in self.stderr:
                return token
        return None


@dataclass
class S3Cli:
    """Thin AWS CLI wrapper that records every invocation for the report."""

    aws_path: str
    region: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def env(self) -> dict[str, str]:
        environment = dict(os.environ)
        if "AWS_CA_BUNDLE" not in environment and Path(_DEFAULT_CA_BUNDLE).exists():
            environment["AWS_CA_BUNDLE"] = _DEFAULT_CA_BUNDLE
        environment.setdefault("AWS_REGION", self.region)
        return environment

    def run(self, *args: str, check: bool = True, note: str = "") -> CliResult:
        command = [self.aws_path, *args]
        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603 - fixed executable, no shell
            command,
            capture_output=True,
            text=True,
            env=self.env(),
            timeout=180,
        )
        result = CliResult(
            args=list(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_s=round(time.monotonic() - started, 3),
        )
        # Never persist raw argv (it can contain bucket-qualified paths only,
        # but keep the record minimal and note-driven anyway).
        self.calls.append(
            {
                "note": note or " ".join(args[:3]),
                "returncode": result.returncode,
                "elapsed_s": result.elapsed_s,
                "error_code": result.error_code(),
            }
        )
        if check and not result.ok:
            raise S3ProbeError(
                f"aws {' '.join(args[:4])} failed ({result.returncode}): "
                f"{result.stderr.strip()[:300]}"
            )
        return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_representative_snapshot(target: Path, source_csv: Path) -> dict[str, Any]:
    """Build a SQLite file sized like a real publication, from the baseline CSV.

    This is deliberately *not* the production schema (that is Task 4). It only
    needs to be a real SQLite database of representative size so the measured
    upload, download, and integrity timings mean something.
    """
    import csv

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    connection = sqlite3.connect(target)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        with source_csv.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            safe = [f"c{i}" for i in range(len(header))]
            connection.execute(
                f"CREATE TABLE results ({', '.join(f'{c} TEXT' for c in safe)})"
            )
            placeholders = ", ".join("?" for _ in safe)
            rows = 0
            for row in reader:
                padded = (row + [None] * len(safe))[: len(safe)]
                connection.execute(
                    f"INSERT INTO results VALUES ({placeholders})", padded
                )
                rows += 1
        connection.execute("CREATE INDEX idx_results_c0 ON results (c0)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    finally:
        connection.close()
    return {
        "path": str(target),
        "rows": rows,
        "byte_size": target.stat().st_size,
        "sha256": sha256_file(target),
        "source_csv": str(source_csv),
    }


def verify_snapshot(path: Path, expected_sha: str, expected_size: int) -> dict[str, Any]:
    """Reader-side verification: size, digest, and SQLite integrity."""
    started = time.monotonic()
    actual_size = path.stat().st_size
    actual_sha = sha256_file(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        row_count = connection.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    finally:
        connection.close()
    return {
        "size_matches": actual_size == expected_size,
        "sha_matches": actual_sha == expected_sha,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "row_count": row_count,
        "verify_elapsed_s": round(time.monotonic() - started, 3),
        "verified": (
            actual_size == expected_size
            and actual_sha == expected_sha
            and integrity == "ok"
            and not foreign_keys
        ),
    }


def manifest_document(
    publication_id: str,
    parent_id: str | None,
    snapshot_key: str,
    version_id: str | None,
    sha256_hex: str,
    byte_size: int,
    summary: dict[str, int],
) -> dict[str, Any]:
    from datetime import UTC, datetime

    return {
        "publication_id": publication_id,
        "parent_publication_id": parent_id,
        "snapshot_key": snapshot_key,
        "snapshot_version_id": version_id,
        "sha256": sha256_hex,
        "byte_size": byte_size,
        "schema_version": 1,
        "created_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "created_by": "feasibility-3.4",
        "summary": summary,
    }


@dataclass
class ProtocolProbe:
    """Drives the full Task 3.4 scenario set against one disposable bucket."""

    cli: S3Cli
    bucket: str
    workdir: Path
    findings: dict[str, Any] = field(default_factory=dict)

    # --- bucket lifecycle -------------------------------------------------

    def create_bucket(self) -> dict[str, Any]:
        started = time.monotonic()
        args = ["s3api", "create-bucket", "--bucket", self.bucket]
        if self.cli.region != "us-east-1":
            args += [
                "--create-bucket-configuration",
                f"LocationConstraint={self.cli.region}",
            ]
        self.cli.run(*args, note="create disposable bucket")
        self.cli.run(
            "s3api",
            "put-public-access-block",
            "--bucket",
            self.bucket,
            "--public-access-block-configuration",
            "BlockPublicAcls=true,IgnorePublicAcls=true,"
            "BlockPublicPolicy=true,RestrictPublicBuckets=true",
            note="block public access",
        )
        self.cli.run(
            "s3api",
            "put-bucket-versioning",
            "--bucket",
            self.bucket,
            "--versioning-configuration",
            "Status=Enabled",
            note="enable versioning",
        )
        self.cli.run(
            "s3api",
            "put-bucket-encryption",
            "--bucket",
            self.bucket,
            "--server-side-encryption-configuration",
            json.dumps(
                {
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "AES256"
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                }
            ),
            note="enable default encryption",
        )
        self.cli.run(
            "s3api",
            "put-bucket-ownership-controls",
            "--bucket",
            self.bucket,
            "--ownership-controls",
            json.dumps({"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}),
            note="bucket-owner-enforced ownership",
        )
        # Confirm the settings actually took effect rather than assuming.
        versioning = self.cli.run(
            "s3api", "get-bucket-versioning", "--bucket", self.bucket,
            note="confirm versioning",
        ).json()
        encryption = self.cli.run(
            "s3api", "get-bucket-encryption", "--bucket", self.bucket,
            note="confirm encryption",
        ).json()
        access = self.cli.run(
            "s3api", "get-public-access-block", "--bucket", self.bucket,
            note="confirm public access block",
        ).json()
        return {
            "bucket": self.bucket,
            "region": self.cli.region,
            "elapsed_s": round(time.monotonic() - started, 3),
            "versioning": (versioning or {}).get("Status"),
            "encryption_algorithm": (
                (encryption or {})
                .get("ServerSideEncryptionConfiguration", {})
                .get("Rules", [{}])[0]
                .get("ApplyServerSideEncryptionByDefault", {})
                .get("SSEAlgorithm")
            ),
            "public_access_blocked": all(
                ((access or {}).get("PublicAccessBlockConfiguration") or {}).values()
            ),
        }

    def delete_bucket(self) -> dict[str, Any]:
        """Remove every version and delete marker, then the bucket itself."""
        deleted = 0
        for listing_command in ("list-object-versions",):
            result = self.cli.run(
                "s3api",
                listing_command,
                "--bucket",
                self.bucket,
                check=False,
                note="list versions for teardown",
            )
            payload = result.json() or {}
            for group in ("Versions", "DeleteMarkers"):
                for item in payload.get(group) or []:
                    self.cli.run(
                        "s3api",
                        "delete-object",
                        "--bucket",
                        self.bucket,
                        "--key",
                        item["Key"],
                        "--version-id",
                        item["VersionId"],
                        check=False,
                        note="delete version",
                    )
                    deleted += 1
        removed = self.cli.run(
            "s3api", "delete-bucket", "--bucket", self.bucket,
            check=False, note="delete bucket",
        )
        return {"versions_deleted": deleted, "bucket_removed": removed.ok}

    # --- protocol primitives ---------------------------------------------

    def put_create_only(
        self, key: str, body: Path, note: str, sha256_b64: str | None = None
    ) -> CliResult:
        """Upload with ``If-None-Match: *`` -- succeeds only if key is absent."""
        args = [
            "s3api", "put-object",
            "--bucket", self.bucket,
            "--key", key,
            "--body", str(body),
            "--if-none-match", "*",
        ]
        if sha256_b64:
            args += ["--checksum-sha256", sha256_b64]
        return self.cli.run(*args, check=False, note=note)

    def put_if_match(self, key: str, body: Path, etag: str, note: str) -> CliResult:
        """Compare-and-swap: replace only if the stored ETag still matches."""
        return self.cli.run(
            "s3api", "put-object",
            "--bucket", self.bucket,
            "--key", key,
            "--body", str(body),
            "--if-match", etag,
            check=False,
            note=note,
        )

    def head(self, key: str, note: str) -> CliResult:
        return self.cli.run(
            "s3api", "head-object", "--bucket", self.bucket, "--key", key,
            check=False, note=note,
        )

    def get(self, key: str, target: Path, note: str) -> CliResult:
        return self.cli.run(
            "s3api", "get-object", "--bucket", self.bucket, "--key", key,
            str(target), check=False, note=note,
        )

    def write_json(self, name: str, document: dict[str, Any]) -> Path:
        path = self.workdir / name
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return path


def new_bucket_name(prefix: str = "xc-feasibility-s3") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def cleanup_workdir(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
