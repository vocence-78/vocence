"""
Integration test for Hippius S3 bucket: credentials and storage code.

Creates a temporary test bucket, uploads a small file, lists objects to verify,
then removes the object and bucket. Skipped unless HIPPIUS_ACCESS_KEY and
HIPPIUS_SECRET_KEY are set (so CI without creds does not run it).

Run with real credentials:
  HIPPIUS_ACCESS_KEY=hip_... HIPPIUS_SECRET_KEY=... pytest tests/test_hippius_bucket.py -v
"""
import os
import tempfile

import pytest


def _should_skip_hippius_test() -> bool:
    access = os.environ.get("HIPPIUS_ACCESS_KEY", "").strip()
    secret = os.environ.get("HIPPIUS_SECRET_KEY", "").strip()
    return not (access and secret)


@pytest.mark.skipif(
    _should_skip_hippius_test(),
    reason="HIPPIUS_ACCESS_KEY and HIPPIUS_SECRET_KEY not set; skipping real bucket test",
)
def test_hippius_bucket_credentials_and_upload_list():
    """Create test bucket, upload a file, list objects; verifies credentials and code."""
    from vocence.adapters.storage import create_storage_client

    bucket_name = "vocence-test-hippius"
    object_name = "test/hello.txt"

    client = create_storage_client()

    # Create bucket if it doesn't exist
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)

    try:
        # Upload a simple file
        content = b"hello hippius\n"
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(content)
            tmp_path = f.name
        try:
            client.fput_object(bucket_name, object_name, tmp_path)
        finally:
            os.unlink(tmp_path)

        # List objects and verify our file is there
        objects = list(client.list_objects(bucket_name, recursive=True))
        names = [obj.object_name for obj in objects]
        assert object_name in names, f"Expected {object_name!r} in {names}"

    finally:
        # Cleanup: remove object then bucket
        try:
            client.remove_object(bucket_name, object_name)
        except Exception:
            pass
        # try:
            # client.remove_bucket(bucket_name)
        # except Exception:
            # pass
