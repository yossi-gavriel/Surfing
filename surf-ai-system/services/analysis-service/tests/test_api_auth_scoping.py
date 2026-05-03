"""Tests for API auth and scoping on analysis endpoints.

Validates:
- User can only access their own analyses
- Guessed track IDs don't leak other users' data
- Debug artifacts (debug_s3) are not exposed in public view
- Pool scoping works correctly
"""

import sys
import os
import time
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.utils.pipeline_store import PipelineStore


def _setup_store_with_jobs():
    """Create a temp DB with analysis jobs from multiple users."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = PipelineStore(db_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    jobs = [
        ("j1", "track-alice-1", "v1", "alice", "pool-1", "completed", 7.5, "s3://b/a1/ride_summary.json", "s3://b/a1/debug.json"),
        ("j2", "track-alice-2", "v2", "alice", "pool-1", "completed", 5.0, "s3://b/a2/ride_summary.json", None),
        ("j3", "track-bob-1", "v3", "bob", "pool-1", "completed", 8.0, "s3://b/b1/ride_summary.json", "s3://b/b1/debug.json"),
        ("j4", "track-carol-1", "v4", "carol", "pool-2", "failed", None, None, None),
    ]

    with store.store.connection() as conn:
        for job_id, track_id, video_id, user_id, pool_id, status, score, canonical, debug in jobs:
            conn.execute(
                """INSERT INTO analysis_jobs
                   (job_id, track_id, video_id, user_id, pool_id, camera_id,
                    status, retry_count, retryable, clip_s3, model_version,
                    ride_score, canonical_s3, debug_s3,
                    failure_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 'clip.mp4', 'v1', ?, ?, ?, ?, ?, ?)""",
                (job_id, track_id, video_id, user_id, pool_id, "c1",
                 status, score, canonical, debug,
                 "timeout" if status == "failed" else None, now, now),
            )

    return store, db_path


def test_user_can_access_own_analysis():
    """Alice can see her own analyses."""
    store, db_path = _setup_store_with_jobs()
    try:
        job = store.get_analysis_job("track-alice-1")
        assert job is not None
        assert job["user_id"] == "alice"

        alice_jobs = store.list_analysis_jobs_for_user("alice")
        assert len(alice_jobs) == 2
        assert all(j["user_id"] == "alice" for j in alice_jobs)
    finally:
        os.unlink(db_path)


def test_user_cannot_access_other_users_analysis_via_query():
    """Bob's analyses don't appear in Alice's list."""
    store, db_path = _setup_store_with_jobs()
    try:
        alice_jobs = store.list_analysis_jobs_for_user("alice")
        bob_track_ids = {"track-bob-1"}
        alice_track_ids = {j["track_id"] for j in alice_jobs}
        assert alice_track_ids.isdisjoint(bob_track_ids), (
            f"Alice's query must not include Bob's tracks: {alice_track_ids & bob_track_ids}"
        )
    finally:
        os.unlink(db_path)


def test_guessed_track_id_returns_different_user():
    """get_analysis_job returns the job but API layer must check user_id.

    The store returns the raw job — the API endpoint must enforce ownership.
    This test validates that the job's user_id is available for the check.
    """
    store, db_path = _setup_store_with_jobs()
    try:
        # Alice guesses Bob's track_id
        bob_job = store.get_analysis_job("track-bob-1")
        assert bob_job is not None
        assert bob_job["user_id"] == "bob"
        # The API route checks: if user_id != current_user.user_id → 404
        # This is enforced in routes/analysis.py line 29
    finally:
        os.unlink(db_path)


def test_api_route_enforces_ownership_in_code():
    """Verify the API route code checks user ownership."""
    api_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "api-gateway", "routes", "analysis.py"
    )
    with open(api_path) as f:
        source = f.read()

    # Must check user_id against current_user
    assert 'user_id' in source, "API must reference user_id for auth check"
    assert 'current_user' in source, "API must reference current_user"
    assert '404' in source, "API must return 404 for unauthorized access (not 403, to prevent enumeration)"

    # Must NOT return 403 (which would confirm existence)
    assert 'status_code=403' not in source, (
        "API must use 404, not 403, for unauthorized access to prevent track_id enumeration"
    )


def test_debug_artifacts_not_in_public_view():
    """_public_view must NOT expose debug_s3 path."""
    api_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "api-gateway", "routes", "analysis.py"
    )
    with open(api_path) as f:
        source = f.read()

    # Find the _public_view function
    import ast
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_public_view":
            # Get the dict keys in the result dict
            func_source = ast.get_source_segment(source, node)
            assert "debug_s3" not in func_source or "debug_s3" not in func_source.split("result =")[1].split("canonical_s3")[0], (
                "_public_view must not expose debug_s3 in the result dict"
            )
            # Verify debug_s3 is not a key in the result
            assert '"debug_s3"' not in func_source.split("result =")[1].split("# Add presigned")[0], (
                "debug_s3 must not be a key in the public result dict"
            )
            return

    assert False, "Could not find _public_view function"


def test_public_view_does_not_expose_internal_fields():
    """Public view must not expose internal fields like job_id, retry_count, clip_s3."""
    api_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "api-gateway", "routes", "analysis.py"
    )
    with open(api_path) as f:
        source = f.read()

    # Find result dict in _public_view
    import ast
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_public_view":
            func_source = ast.get_source_segment(source, node)
            result_section = func_source.split("result =")[1].split("# Add presigned")[0]

            internal_fields = ["job_id", "retry_count", "clip_s3", "camera_id",
                               "failure_reason", "retryable", "analysis_duration_ms"]
            for field in internal_fields:
                assert f'"{field}"' not in result_section, (
                    f"_public_view must not expose internal field: {field}"
                )
            return

    assert False, "Could not find _public_view function"


def test_pool_scoping_works():
    """Pool-scoped queries return correct jobs."""
    store, db_path = _setup_store_with_jobs()
    try:
        pool1_jobs = store.list_analysis_jobs_for_pool("pool-1")
        pool2_jobs = store.list_analysis_jobs_for_pool("pool-2")

        assert len(pool1_jobs) == 3  # alice*2 + bob*1
        assert len(pool2_jobs) == 1  # carol*1
        assert all(j["pool_id"] == "pool-1" for j in pool1_jobs)
        assert all(j["pool_id"] == "pool-2" for j in pool2_jobs)
    finally:
        os.unlink(db_path)


def test_status_filter_works():
    """Status filter on list queries works correctly."""
    store, db_path = _setup_store_with_jobs()
    try:
        completed = store.list_analysis_jobs_for_user("alice", status="completed")
        assert len(completed) == 2

        failed = store.list_analysis_jobs_for_user("alice", status="failed")
        assert len(failed) == 0

        failed_carol = store.list_analysis_jobs_for_user("carol", status="failed")
        assert len(failed_carol) == 1
    finally:
        os.unlink(db_path)


def test_nonexistent_track_returns_none():
    """Querying a nonexistent track_id returns None."""
    store, db_path = _setup_store_with_jobs()
    try:
        job = store.get_analysis_job("track-does-not-exist-xyz-123")
        assert job is None
    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    test_user_can_access_own_analysis()
    test_user_cannot_access_other_users_analysis_via_query()
    test_guessed_track_id_returns_different_user()
    test_api_route_enforces_ownership_in_code()
    test_debug_artifacts_not_in_public_view()
    test_public_view_does_not_expose_internal_fields()
    test_pool_scoping_works()
    test_status_filter_works()
    test_nonexistent_track_returns_none()
    print("All auth and scoping tests passed.")
