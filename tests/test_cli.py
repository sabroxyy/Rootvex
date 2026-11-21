from typer.testing import CliRunner

from netwarden.cli import app

runner = CliRunner()


def test_devices_with_example_inventory():
    r = runner.invoke(app, ["devices", "--fixtures"])
    assert r.exit_code == 0 and "R1" in r.output and "10.99.0.11" in r.output


def test_check_allowed_and_denied():
    assert runner.invoke(app, ["check", "R1", "show ip ospf neighbor", "--fixtures"]).exit_code == 0
    r = runner.invoke(app, ["check", "R1", "configure terminal", "--fixtures"])
    assert r.exit_code == 1 and "DENIED" in r.output
