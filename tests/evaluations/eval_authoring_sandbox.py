"""Opt-in real-container authoring checks; never uses the running app or bridge."""

import asyncio
import os
import secrets
import subprocess
import threading

import nibabel as nib
import numpy as np
import pytest
from api_service.assistant.tools.definition import ToolExecutionContext
from api_service.assistant.tools.probe_tools import AssistantProbeTools
from api_service.assistant.tools.workflow_test_tools import AssistantWorkflowTestTools
from neurocade_runtime_tools.bridge import BridgeRuntime
from neurocade_runtime_tools.bridge_server import BridgeHTTPServer

from backend_common.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get("RUN_AUTHORING_SANDBOX") != "1", reason="Opt in to isolated Docker checks")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    for image in ("vnmd/fsl_6.0.7.23:20261013", "vnmd/freesurfer_8.2.0:20261005"):
        result = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
        if result.returncode:
            pytest.skip("Cache the benchmark images before running; this test does not install them")
    runtime = BridgeRuntime(backend="docker", data_root=tmp_path, image_dir=tmp_path / "images", launch_id="authoring-test")
    token = secrets.token_hex(32)
    token_path = tmp_path / "token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    server = BridgeHTTPServer(("127.0.0.1", 0), runtime, token, launch_id="authoring-test")
    monkeypatch.setenv("HOST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NEUROCADE_RUNTIME", "docker")
    monkeypatch.setenv("NEUROCADE_LAUNCH_ID", "authoring-test")
    monkeypatch.setenv("NEUROCADE_BRIDGE_TOKEN_FILE", str(token_path))
    monkeypatch.setenv("NEUROCADE_BRIDGE_URL", f"http://127.0.0.1:{server.server_port}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Settings(_env_file=None, HOST_DATA_DIR=tmp_path), tmp_path
    finally:
        runtime.shutdown()
        server.shutdown()
        server.server_close()


def test_inspect_and_real_workflow_cannot_access_host_case(sandbox, monkeypatch):
    settings, root = sandbox
    context = ToolExecutionContext("sandbox")
    current_case = root / "current-case"
    current_case.mkdir()
    sentinel = current_case / "sentinel"
    sentinel.write_text("protected")
    nib.save(nib.Nifti1Image(np.arange(120, dtype=np.float32).reshape(2, 3, 4, 5), np.eye(4)), current_case / "input.nii.gz")
    tools = AssistantWorkflowTestTools(settings=settings)
    monkeypatch.setattr(tools, "identity", lambda state: (current_case, {"user_id": "test", "case_id": "test", "workspace_id": "test"}))
    definition = dict(
        id="mean",
        image="vnmd/fsl_6.0.7.23:20261013",
        description="mean",
        details="mean",
        inputs=[dict(name="input", description="input")],
        case_output_folder="mean",
        outputs=[dict(name="mean", type="intensity_volume", path="mean.nii.gz", description="mean", required=True)],
        script='fslmaths "${INPUTS[0]}" -Tmean "${OUTPUTS[0]}"',
    )

    async def run():
        inspected = await AssistantProbeTools(settings=settings).inspect(
            {}, context, {"image": definition["image"], "executable": "fslmaths", "action": "help", "arguments": ["-h"]}
        )
        assert "-Tmean" in inspected.details["stdout"]
        for expected, script, timeout in (
            ("completed", definition["script"], 10),
            ("failed", "echo early-failure >&2; exit 4", 10),
            ("failed", f'echo overwritten > "{sentinel}"', 10),
            ("startup_timeout", 'for i in $(seq 1 30); do echo "line $i"; done; echo waiting >&2; sleep 30', 1),
        ):
            result = await tools.test(
                {}, context, {"definition": {**definition, "script": script}, "inputs": ["input.nii.gz"], "timeout_s": timeout}
            )
            for _ in range(20):
                if result.details.get("status") not in {"running", "preparing"}:
                    break
                await asyncio.sleep(1)
                result = await tools.test({}, context, {"mode": "status", "test_id": result.details["test_id"]})
            assert result.details["status"] == expected, result.content
            assert result.details["writer_stopped"] is True
            assert sentinel.read_text() == "protected"
            if expected == "startup_timeout":
                assert result.details["stdout_tail"].splitlines() == [f"line {i}" for i in range(16, 31)]
                logs = await tools.test({}, context, {"mode": "logs", "test_id": result.details["test_id"]})
                assert logs.details["content"].splitlines()[0] == "line 1"
                assert logs.details["eof"]
        assert not list(root.glob(".workflow-test-*/case with spaces"))

    asyncio.run(run())
