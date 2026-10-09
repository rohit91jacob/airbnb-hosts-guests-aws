"""Local stand-in for AWS Step Functions: walk the real state machine definition.

`infra/statemachine/pipeline.asl.json` is the definition Terraform deploys. Every Task state
runs the container with `["airbnb", <sub-command>, ...]`. Locally we interpret the same file:
start at `StartAt`, run each Task's command in-process, follow `Next`, and on failure follow
the `Catch` route (NotifyFailure -> Fail) exactly as Step Functions would. So the order of
steps and the data-quality gates cannot drift between local runs and the deployed pipeline.

Supported subset: Task (ECS runTask / SNS publish), Succeed, Fail. Retries are not emulated:
locally a failure is reported immediately.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from airbnb_lakehouse.config import REPO_ROOT

log = logging.getLogger(__name__)

ASL_PATH = REPO_ROOT / "infra" / "statemachine" / "pipeline.asl.json"
_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def load_definition(path: Path = ASL_PATH) -> dict:
    """Parse the ASL file, filling Terraform placeholders with inert local values."""
    text = path.read_text(encoding="utf-8")
    return json.loads(_PLACEHOLDER.sub(lambda m: f"local-{m.group(1)}", text))


def task_command(state: dict) -> list[str] | None:
    try:
        return state["Parameters"]["Overrides"]["ContainerOverrides"][0]["Command"]
    except (KeyError, IndexError, TypeError):
        return None


@dataclass
class StepRecord:
    state: str
    command: list[str] | None
    exit_code: int
    seconds: float


@dataclass
class Execution:
    status: str = "RUNNING"
    steps: list[StepRecord] = field(default_factory=list)
    failed_state: str | None = None
    exit_code: int = 0


def run_definition(
    definition: dict,
    run_command: Callable[[list[str]], int],
    *,
    rewrite: Callable[[list[str]], list[str]] = lambda c: c,
    start_at: str | None = None,
) -> Execution:
    states = definition["States"]
    name = start_at or definition["StartAt"]
    ex = Execution()
    error_code = 0
    while True:
        state = states[name]
        kind = state["Type"]
        if kind == "Succeed":
            ex.status = "SUCCEEDED"
            return ex
        if kind == "Fail":
            ex.status = "FAILED"
            ex.exit_code = error_code or 1
            return ex
        if kind != "Task":
            raise ValueError(f"state {name}: type {kind} is not supported by the local runner")
        cmd = task_command(state)
        t0 = time.monotonic()
        if cmd is None:  # e.g. the SNS NotifyFailure task
            log.error("state machine: %s (%s)", name, state.get("Comment", ""))
            code = 0
        else:
            local_cmd = rewrite(list(cmd))
            log.info("state machine: %s -> %s", name, " ".join(local_cmd))
            try:
                code = run_command(local_cmd[1:])
            except Exception:  # a crash fails the step like a non-zero container exit
                log.exception("state %s crashed", name)
                code = 1
        ex.steps.append(StepRecord(name, cmd, code, round(time.monotonic() - t0, 1)))
        if code != 0:
            ex.failed_state = name
            error_code = code
            catch = next(iter(state.get("Catch", [])), None)
            if catch is None:
                ex.status = "FAILED"
                ex.exit_code = code
                return ex
            name = catch["Next"]
            continue
        name = state["Next"]
