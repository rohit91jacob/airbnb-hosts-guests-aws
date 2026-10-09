"""The local runner walks the same Step Functions definition Terraform deploys."""

import re
from pathlib import Path

from airbnb_lakehouse.cli import build_parser
from airbnb_lakehouse.orchestrate import ASL_PATH, load_definition, run_definition, task_command

INFRA = Path(__file__).resolve().parents[2] / "infra"


def test_every_task_is_a_valid_cli_command():
    d = load_definition()
    parser = build_parser()
    commands = [task_command(s) for s in d["States"].values() if s["Type"] == "Task"]
    commands = [c for c in commands if c]
    assert [c[1] for c in commands] == [
        "discover", "ingest", "dq", "bronze", "dq", "transform", "dq", "report",
    ]  # fmt: skip
    for c in commands:
        assert c[0] == "airbnb"
        parser.parse_args(c[1:])  # raises SystemExit on an unknown flag


def test_quality_gates_sit_between_layers():
    d = load_definition()
    order, name = [], d["StartAt"]
    while d["States"][name]["Type"] == "Task":
        order.append(name)
        name = d["States"][name]["Next"]
    assert order.index("LandingDQ") < order.index("LoadBronze") < order.index("BronzeDQ")
    assert order.index("BronzeDQ") < order.index("Transform") < order.index("MartsDQ")
    assert order.index("MartsDQ") < order.index("Report")


def test_happy_path_runs_in_order():
    calls = []
    ex = run_definition(load_definition(), lambda argv: calls.append(argv) or 0)
    assert ex.status == "SUCCEEDED"
    assert calls[0] == ["discover"] and calls[-1] == ["report"]


def test_failed_gate_routes_to_notify_and_fail_with_its_exit_code():
    calls = []

    def run(argv):
        calls.append(argv)
        return 3 if argv == ["dq", "--layer", "bronze"] else 0

    ex = run_definition(load_definition(), run)
    assert ex.status == "FAILED"
    assert ex.failed_state == "BronzeDQ"
    assert ex.exit_code == 3
    assert ["transform", "--target", "athena"] not in calls  # nothing after the gate
    assert ex.steps[-1].state == "NotifyFailure"


def test_crash_is_a_failure():
    def boom(argv):
        raise RuntimeError("container died")

    ex = run_definition(load_definition(), boom)
    assert (ex.status, ex.failed_state, ex.exit_code) == ("FAILED", "Discover", 1)


def test_terraform_supplies_every_asl_placeholder():
    placeholders = set(re.findall(r"\$\{(\w+)\}", ASL_PATH.read_text(encoding="utf-8")))
    root = (INFRA / "main.tf").read_text(encoding="utf-8")
    assert re.search(
        r'definition_path\s*=\s*"\$\{path\.module\}/statemachine/pipeline\.asl\.json"', root
    )
    tf = (INFRA / "modules" / "orchestration" / "main.tf").read_text(encoding="utf-8")
    block = re.search(r"templatefile\(var\.definition_path,\s*\{(.*?)\}\)", tf, re.S)
    assert block, "orchestration module must render the ASL file with templatefile()"
    supplied = set(re.findall(r"^\s*(\w+)\s*=", block.group(1), re.M))
    assert placeholders == supplied
