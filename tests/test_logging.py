import json

import structlog

from netwarden import log


def test_log_lines_are_json_and_redacted(tmp_path):
    log_file = log.setup_logging(tmp_path)
    log.register_secret("Sup3rS3cret!")
    structlog.contextvars.bind_contextvars(run_id="abc123")
    log.get_logger().info(
        "device_output",
        output="username admin password 7 0822455D0A16\nenable secret 5 $1$xyz\nSup3rS3cret!",
    )
    structlog.contextvars.clear_contextvars()

    line = json.loads(log_file.read_text().strip().splitlines()[-1])
    assert line["run_id"] == "abc123"
    assert "0822455D0A16" not in line["output"]
    assert "$1$xyz" not in line["output"]
    assert "Sup3rS3cret!" not in line["output"]
    assert "timestamp" in line


def test_redact_keeps_normal_text():
    assert log.redact_text("show ip ospf neighbor") == "show ip ospf neighbor"
