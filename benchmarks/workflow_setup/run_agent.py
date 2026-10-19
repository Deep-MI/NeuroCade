"""Run the real assistant against copied fixtures, never a running NeuroCade app."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def verify_fixtures(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    expected = manifest["files_sha256"]
    actual = {}
    for task in ("blur", "thalamus"):
        directory = root / task
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Missing or linked fixture directory: {task}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlink in fixtures: {path.relative_to(root)}")
            if path.is_file():
                with path.open("rb") as stream:
                    actual[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("Fixture files do not match manifest; prepare fresh fixtures")
    return manifest


def summarize(result: dict, events: list[dict]) -> dict:
    calls = [event["payload"] for event in events if event["event"] == "tool_call"]
    approval = result.get("approval_request") or {}
    return {
        "setup_approval_reached": approval.get("name") == "tool_config_upsert",
        "tool_calls": len(calls),  # Approval requests are reported separately, not executed.
        "failed_calls": sum(bool(call.get("is_error")) for call in calls),
        "model_rounds": max((event["payload"].get("round", 0) for event in events), default=0),
        "reasoning_events": sum(event["event"] == "reasoning" for event in events),
        "staging_timeouts_s": [call["arguments"].get("timeout_s", 10) for call in calls
                               if call["name"] == "tool_workflow_test"
                               and call["arguments"].get("mode", "staging") == "staging"],
    }


async def run(args: argparse.Namespace) -> None:
    # Configure isolation BEFORE importing any application singleton/settings/DB.
    from dotenv import dotenv_values

    fixtures, output = args.fixtures.resolve(), args.output.resolve()
    manifest = verify_fixtures(fixtures)
    if output.is_relative_to(fixtures):
        raise ValueError("Output must be outside the fixture directory")
    output.mkdir(parents=True, exist_ok=False)
    for key, value in dotenv_values(args.env_file).items():
        if value is not None and key.startswith(("LLM_", "ASSISTANT_")):
            os.environ[key] = value
    os.environ.update(
        HOST_DATA_DIR=str(output / "data"),
        DATABASE_URL="sqlite+pysqlite:///" + str(output / "trial.db"),
        NEUROCADE_RUNTIME="docker", NEUROCADE_LAUNCH_ID="isolated-benchmark",
    )
    os.chdir(output)  # Prevent implicit loading of the repository .env.
    for path in (REPO, REPO / "api-service", REPO / "packages/neurocade-runtime-tools/src"):
        sys.path.insert(0, str(path))

    from neurocade_runtime_tools.bridge import BridgeRuntime
    from neurocade_runtime_tools.bridge_server import BridgeHTTPServer

    (output / "data").mkdir()
    bridge = BridgeRuntime(backend="docker", data_root=output / "data",
                           image_dir=output / "images", launch_id="isolated-benchmark")
    token = secrets.token_hex(32)
    token_path = output / "token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    server = BridgeHTTPServer(("127.0.0.1", 0), bridge, token, launch_id="isolated-benchmark")
    os.environ.update(NEUROCADE_BRIDGE_URL=f"http://127.0.0.1:{server.server_port}",
                      NEUROCADE_BRIDGE_TOKEN_FILE=str(token_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    from api_service.assistant.runtime import AssistantRuntime
    from api_service.assistant.tools.workflow_test_tools import _TASKS
    from api_service.runtime.gui_runtime import GuiRuntime

    from backend_common import sample_seed
    from backend_common.auth import AuthContext
    from backend_common.case_storage import ensure_case_storage_layout
    from backend_common.db import Base, RoleEnum, SessionLocal, User, Workspace, engine
    from backend_common.providers import provider_registry
    from backend_common.settings import get_settings
    from benchmarks.workflow_setup.benchmark import snapshot
    settings = get_settings()
    config = provider_registry.get()
    prompts = json.loads((REPO / "benchmarks/workflow_setup/prompts.json").read_text())
    report = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "model": config.model, "provider": config.provider,
        "thinking_disabled": settings.llm_disable_thinking,
        "max_rounds": settings.assistant_max_rounds, "benchmark_timeout_s": args.timeout,
        "trials": {},
    }
    write_json(output / "fixture_manifest.json", manifest)
    cases = {}

    async def stop_tests():
        pending = list(_TASKS.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.to_thread(bridge.shutdown)

    try:
        Base.metadata.create_all(engine)
        for task in (prompts if args.task == "both" else [args.task]):
            trial = output / task
            trial.mkdir()
            sample_seed.SAMPLE_CASE_ROOT = fixtures / task
            events = []

            async def sink(event, payload, *, events=events, trial=trial):
                entry = {"event": event, "payload": payload}
                events.append(entry)
                with (trial / "events.jsonl").open("a") as stream:
                    stream.write(json.dumps(entry, default=str) + "\n")

            with SessionLocal() as db:
                user = User(id="benchmark-" + task, external_auth_id="benchmark-" + task,
                            email=task + "@example.com", full_name="Benchmark")
                db.add(user)
                db.flush()
                workspace = Workspace(id="workspace-" + task, owner_user_id=user.id,
                                      name="benchmark-" + task, kind="shared", is_default=True)
                db.add(workspace)
                db.flush()
                case = sample_seed.ensure_sample_case(db, user)
                if case is None:
                    raise RuntimeError("Sample case could not be prepared")
                case_path = ensure_case_storage_layout(settings, case, workspace)
                cases[task] = (case_path, snapshot(case_path))
                write_json(trial / "case_baseline.json", cases[task][1])
                db.commit()
                (trial / "prompt.txt").write_text(prompts[task])
                started = time.monotonic()
                print(f"START {task}", flush=True)
                result = {}
                error = None
                try:
                    result = await asyncio.wait_for(
                        AssistantRuntime(GuiRuntime()).run_chat(
                            db=db, context=AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"),
                            messages=[{"role": "user", "content": prompts[task]}],
                            workspace_id=workspace.id, case_id=case.id, scope="case",
                            provider=None, model=None, gui_session_id="benchmark-" + task,
                            require_tool_approval=True, event_sink=sink, persist=True,
                        ), timeout=args.timeout,
                    )
                    write_json(trial / "result.json", result)
                    (trial / "answer.md").write_text(result["message"]["content"])
                except Exception as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)}
                summary = summarize(result, events)
                summary.update(elapsed_s=round(time.monotonic() - started, 2), error=error)
                report["trials"][task] = summary
                write_json(trial / "summary.json", summary)
                print(f"DONE {task} {json.dumps(summary)}", flush=True)
                await stop_tests()
    finally:
        # Do not leave a test running after its agent finishes or the runner is interrupted.
        await stop_tests()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        token_path.unlink(missing_ok=True)
        for task, (case_path, baseline) in cases.items():
            report["trials"].setdefault(task, {})["case_files_unchanged"] = snapshot(case_path) == baseline
        report["fixtures_unchanged"] = verify_fixtures(fixtures) == manifest
        report["durable_tests"] = [json.loads(path.read_text()) for path in
                                   sorted((output / "data").glob(".workflow-test-*/result.json"))]
        write_json(output / "report.json", report)
        engine.dispose()
    print(f"Report: {output / 'report.json'}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; never reused")
    parser.add_argument("--env-file", type=Path, default=REPO / ".env",
                        help="Only LLM_* and ASSISTANT_* settings are read")
    parser.add_argument("--task", choices=("both", "blur", "thalamus"), default="both")
    parser.add_argument("--timeout", type=int, default=1200, help="Wall-clock seconds per agent trial")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
