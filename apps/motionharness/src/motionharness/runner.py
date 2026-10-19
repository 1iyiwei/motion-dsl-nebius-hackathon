"""Turn a candidate into one sandbox job, and the job's output back into a Report."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from .plugin import Report, Source, Task, TestCase, package_files
from .sandbox import Checkpoint, Job, RunResult, Sandbox, _q

RESULT = "out/result.json"


def job_for(task: Task, src: Source, sandbox: Sandbox, base: Checkpoint, label: str, timeout: float = 600) -> Job:
    files = {f"plugin/{k}": v.encode() for k, v in package_files(task, src).items()}
    files["demo.motion.json"] = src.demo.encode()
    py = sandbox.python if sandbox.python == "python" else _q(sandbox.python)
    test = f"{py} -m pytest -q -p no:cacheprovider -o addopts=''"
    shell = "\n".join([
        f"export MH_VERB={_q(task.verb)} PYTHONHASHSEED=0",
        "rm -rf out site && mkdir -p out",
        f"{sandbox.install_cmd('plugin')} > out/install.log 2>&1; echo $? > out/install.rc",
        f"MOTIONLANG_CONTRACT_VERB={_q(task.verb)} {test} test_contract.py --junitxml=out/contract.xml > out/contract.log 2>&1; echo $? > out/contract.rc",
        f"{test} --rootdir=. plugin/tests/{task.test_file} --junitxml=out/task.xml > out/task.log 2>&1; echo $? > out/task.rc",
        f"{py} probe.py > out/probe.log 2>&1",
        "cat out/probe.log",
    ])
    return Job(base, files, shell, collect=[RESULT], timeout=timeout, label=label)


def _cases(suite: str, xml: str) -> list[TestCase]:
    if not xml.strip():
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for tc in root.iter("testcase"):
        name = tc.get("name", "?")
        outcome, msg = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = tc.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                msg = (el.get("message") or "") + ("\n" + el.text if el.text else "")
                break
        out.append(TestCase(suite, name, outcome, msg.strip()[-3000:]))
    return out


def parse_result(task: Task, r: RunResult) -> Report:
    rep = Report()
    if r.error:
        rep.platform_error = r.error
        return rep
    rep.timed_out = r.exit_code == 124 or "[timed out" in r.stderr
    raw = r.files.get(RESULT)
    if raw is None and rep.timed_out:
        return rep
    if raw is None:
        rep.platform_error = f"no result bundle (exit {r.exit_code}): {(r.stderr or r.stdout)[-1500:]}"
        return rep
    try:
        bundle = json.loads(raw)
    except ValueError:
        rep.platform_error = "result bundle is not JSON"
        return rep
    rep.install_ok = bundle.get("install", {}).get("rc") == 0
    rep.install_log = bundle.get("install", {}).get("log", "")
    rep.cases = _cases("contract", bundle.get("contract", {}).get("xml", "")) + _cases("task", bundle.get("task", {}).get("xml", ""))
    rep.logs = {s: bundle.get(s, {}).get("log", "") for s in ("contract", "task")}
    rep.probe = bundle.get("probe") or {}
    rep.probe.setdefault("verb", task.verb)
    return rep
