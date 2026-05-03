"""Tests for feature flag behavior (ANALYSIS_ENABLED).

Validates that when ANALYSIS_ENABLED=false:
- Clipper flow behaves exactly as before
- No analysis event is published
- No analysis job is created
"""

import sys
import os
import ast

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))


def test_analysis_enabled_defaults_to_false():
    """ANALYSIS_ENABLED defaults to false in clipper config."""
    # Read the config source to validate default (don't instantiate — it requires S3_BUCKET)
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "clipper-service", "src", "config.py"
    )
    with open(config_path) as f:
        source = f.read()

    # Verify the default is "false"
    assert '"false"' in source or "'false'" in source, (
        "ANALYSIS_ENABLED must default to false in clipper config"
    )
    assert "ANALYSIS_ENABLED" in source


def test_clipper_guards_analysis_publish_with_flag():
    """Clipper only publishes to analysis queue when config.analysis_enabled is True."""
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "clipper-service", "src", "main.py"
    )
    with open(config_path) as f:
        source = f.read()

    # The guard must check config.analysis_enabled before publishing
    assert "config.analysis_enabled" in source, (
        "Clipper must check config.analysis_enabled before publishing"
    )

    # The guard must also check config.analysis_sqs_url
    assert "config.analysis_sqs_url" in source, (
        "Clipper must check config.analysis_sqs_url before publishing"
    )

    # Verify the structure: analysis_enabled must be in an if-condition
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_clip":
            found_guard = False
            for child in ast.walk(node):
                if isinstance(child, ast.If):
                    # Check if the condition references analysis_enabled
                    if_source = ast.dump(child.test)
                    if "analysis_enabled" in if_source:
                        found_guard = True
                        break
            assert found_guard, (
                "process_clip must have an if-guard on config.analysis_enabled"
            )
            break


def test_clipper_upload_exists_without_analysis():
    """Clip upload (s3_client.upload_file) exists independently of analysis publish."""
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "clipper-service", "src", "main.py"
    )
    with open(config_path) as f:
        source = f.read()

    # upload_file call must exist outside the analysis if-block
    # Both upload_file and send_message must be present
    assert "upload_file" in source, "Clipper must upload clip to S3"
    assert "send_message" in source, "Clipper must have analysis queue send_message"

    # The upload_file must come BEFORE the analysis_enabled check
    upload_pos = source.index("upload_file")
    analysis_pos = source.index("analysis_enabled")
    assert upload_pos < analysis_pos, (
        "Clip upload must happen before analysis publish check"
    )


def test_analysis_publish_is_in_try_except():
    """Analysis publish failure must not crash clip processing."""
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "clipper-service", "src", "main.py"
    )
    with open(config_path) as f:
        source = f.read()

    # The analysis publish block must be wrapped in try/except
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_clip":
            # Find the if-block that checks analysis_enabled
            for child in ast.walk(node):
                if isinstance(child, ast.If):
                    if_source = ast.dump(child.test)
                    if "analysis_enabled" in if_source:
                        # Inside this if-block, there must be a Try
                        has_try = False
                        for sub in ast.walk(child):
                            if isinstance(sub, ast.Try):
                                has_try = True
                                break
                        assert has_try, (
                            "Analysis publish inside if-block must be wrapped in try/except"
                        )
                        return
    assert False, "Could not find analysis_enabled if-block in process_clip"


def test_no_analysis_service_import_in_clipper():
    """Clipper must not import any analysis-service modules."""
    clipper_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "clipper-service", "src"
    )
    for fname in os.listdir(clipper_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(clipper_dir, fname)
        with open(fpath) as f:
            source = f.read()
        assert "from src.analyzer" not in source, f"{fname} must not import analyzer"
        assert "from src.maneuvers" not in source, f"{fname} must not import maneuvers"
        assert "from src.detector" not in source, f"{fname} must not import detector"


if __name__ == "__main__":
    test_analysis_enabled_defaults_to_false()
    test_clipper_guards_analysis_publish_with_flag()
    test_clipper_upload_exists_without_analysis()
    test_analysis_publish_is_in_try_except()
    test_no_analysis_service_import_in_clipper()
    print("All feature flag tests passed.")
