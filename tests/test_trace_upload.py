"""Regression tests for issue archive scrubbing."""

from __future__ import annotations

import tarfile

from kodo.trace_upload import pack_run_archive


def test_pack_run_archive_scrubs_log_sensitive_data(tmp_path):
    """Fake card data and .env-style secrets in logs are redacted in the archive."""
    run_dir = tmp_path / "20260101_120000"
    run_dir.mkdir()
    (run_dir / "log.jsonl").write_text(
        "\n".join(
            [
                '{"event":"note","message":"safe marker stays visible"}',
                '{"event":"agent","message":"card 4111111111111111 should not survive"}',
                '{"event":"agent","message":"OPENAI_API_KEY=sk-test-1234567890\\nSECRET_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    archive_result = pack_run_archive(run_dir)
    archive_path = archive_result.path

    with tarfile.open(archive_path, "r:gz") as tar:
        log_payload = tar.extractfile("log.jsonl").read().decode("utf-8")

    assert "safe marker stays visible" in log_payload
    assert "4111111111111111" not in log_payload
    assert "sk-test-" not in log_payload
    assert "OPENAI_API_KEY=sk-test-1234567890" not in log_payload
    assert "SECRET_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" not in log_payload
    assert "[secret-redacted]" in log_payload
    assert "redacted" in log_payload
    assert archive_result.stats.redactions >= 2
    assert archive_result.stats.files_changed == 1
