"""Authenticated application-side client for the native runtime bridge."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import requests

from .execution import (
    RuntimeBind,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    cancellation_observer,
    runtime_progress_observer,
)
from .protocol import BUILD_VERSION, PROTOCOL_VERSION, TERMINAL_RUN_STATES, RunState, RuntimeImageSpec, relative_to_data_root


class BridgeError(RuntimeError):
    pass


class RuntimeGpuUnavailableError(RuntimeError):
    pass


class BridgeClient:
    def __init__(self, base_url: str, token: str, data_root: Path, *, poll_interval_s: float = 0.25) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.data_root = data_root.expanduser().resolve()
        self.poll_interval_s = poll_interval_s
        self.session = requests.Session()
        if not self.token:
            raise BridgeError("Runtime bridge token is empty")

    @classmethod
    def from_environment(cls) -> BridgeClient:
        url = os.environ.get("NEUROCADE_BRIDGE_URL", "").strip()
        token_file = os.environ.get("NEUROCADE_BRIDGE_TOKEN_FILE", "").strip()
        data_root = os.environ.get("HOST_DATA_DIR", "").strip()
        if not url or not token_file or not data_root:
            raise BridgeError("NEUROCADE_BRIDGE_URL, NEUROCADE_BRIDGE_TOKEN_FILE, and HOST_DATA_DIR are required")
        return cls(url, Path(token_file).read_text(encoding="utf-8"), Path(data_root))

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        timeout = kwargs.pop("request_timeout", 10)
        attempts = max(1, int(kwargs.pop("attempts", 3)))
        response = None
        last_error: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    timeout=timeout,
                    **kwargs,
                )
                if response.status_code not in {502, 503, 504} or attempt + 1 == attempts:
                    break
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 == attempts:
                    raise BridgeError(f"Runtime bridge is unavailable: {exc}") from exc
            time.sleep(0.2 * (attempt + 1))
        if response is None:
            assert last_error is not None
            raise BridgeError(f"Runtime bridge is unavailable: {last_error}") from last_error
        if response.status_code >= 400:
            try:
                detail = response.json().get("error")
            except ValueError:
                detail = response.text
            raise BridgeError(f"Runtime bridge returned {response.status_code}: {detail or 'request failed'}")
        return response.json()

    def health(self) -> dict[str, Any]:
        payload = self._request("GET", "/v1/health")
        if payload.get("protocol_version") != PROTOCOL_VERSION:
            raise BridgeError(f"Incompatible runtime bridge protocol: {payload.get('protocol_version')!r}")
        return payload

    def resolve_capability(self, image: object) -> dict[str, Any]:
        value = image.to_dict() if isinstance(image, RuntimeImageSpec) else image
        preparation_timeout = max(60, int(os.environ.get("NEUROCADE_BRIDGE_PREPARE_TIMEOUT_SECONDS", "7200")))
        return self._request(
            "POST",
            "/v1/capabilities/resolve",
            json={"protocol_version": PROTOCOL_VERSION, "image": value},
            request_timeout=(10, preparation_timeout),
        )

    def cancel(self, run_id: str) -> None:
        self._request("DELETE", f"/v1/runs/{run_id}")

    def _cancel_best_effort(self, run_id: str) -> None:
        with suppress(BridgeError):
            self._request("DELETE", f"/v1/runs/{run_id}", request_timeout=2, attempts=1)

    def _payload(self, request: RuntimeExecutionRequest) -> dict[str, Any]:
        run = request.container_run
        assert run is not None and run.run_id is not None
        binds = []
        for bind in run.binds:
            if not isinstance(bind, RuntimeBind):
                raise TypeError("Application requests require host-path runtime binds")
            binds.append({
                "source_relative": relative_to_data_root(bind.host_path, self.data_root, label="Bind source"),
                "container_path": bind.container_path,
                "mode": bind.mode,
            })
        return {
            "protocol_version": PROTOCOL_VERSION,
            "build_version": BUILD_VERSION,
            "run_id": run.run_id,
            "container": {
                "image": run.image.to_dict(), "command": list(run.command), "binds": binds,
                "env": dict(run.env or {}), "cwd": run.cwd,
                "network_disabled": run.network_disabled, "gpu_enabled": run.gpu_enabled,
                "isolated": run.isolated,
            },
            "timeout_s": request.timeout_s,
            "workdir_relative": relative_to_data_root(request.cwd, self.data_root, label="Working directory") if request.cwd else None,
            "stdout_relative": relative_to_data_root(request.stdout_path, self.data_root, label="stdout log") if request.stdout_path else None,
            "stderr_relative": relative_to_data_root(request.stderr_path, self.data_root, label="stderr log") if request.stderr_path else None,
            "capture_output": request.capture_output,
            "check": request.check,
            "log_lines": list(request.log_lines),
        }

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        payload = self._payload(request)
        run_id = str(payload["run_id"])
        submitted = False
        terminal = False
        try:
            self._request("POST", "/v1/runs", json=payload)
            submitted = True
            observer = cancellation_observer.get()
            if observer is not None:
                observer(lambda: self.cancel(run_id))
            # Tool timeouts begin after image preparation in the bridge. A first-use
            # multi-GB image pull must not consume the tool's execution budget.
            preparation_timeout = max(60, int(os.environ.get("NEUROCADE_BRIDGE_PREPARE_TIMEOUT_SECONDS", "7200")))
            disconnect_grace = max(5, int(os.environ.get("NEUROCADE_BRIDGE_DISCONNECT_GRACE_SECONDS", "30")))
            deadline = time.monotonic() + preparation_timeout + (request.timeout_s or 0) + 15
            disconnected_at: float | None = None
            last_progress: dict[str, Any] | None = None
            last_progress_publish_at = 0.0
            while True:
                try:
                    state = self._request("GET", f"/v1/runs/{run_id}")
                    disconnected_at = None
                except BridgeError:
                    disconnected_at = disconnected_at or time.monotonic()
                    if time.monotonic() - disconnected_at >= disconnect_grace:
                        raise
                    time.sleep(self.poll_interval_s)
                    continue
                try:
                    run_state = RunState(str(state.get("state")))
                except ValueError as exc:
                    raise BridgeError(f"Runtime bridge returned an invalid run state: {state.get('state')!r}") from exc
                progress = state.get("progress")
                now = time.time()
                should_publish_progress = (
                    isinstance(progress, dict)
                    and (
                        progress != last_progress
                        or now - last_progress_publish_at >= 5.0
                    )
                )
                if should_publish_progress:
                    assert isinstance(progress, dict)
                    observer = runtime_progress_observer.get()
                    if observer is not None:
                        update = dict(progress)
                        updated_at = update.get("updated_at")
                        if isinstance(updated_at, (int, float)):
                            update["stalled_seconds"] = max(0, int(now - updated_at))
                        update["process_active"] = run_state not in TERMINAL_RUN_STATES
                        observer(update)
                    last_progress = dict(progress)
                    last_progress_publish_at = now
                if run_state in TERMINAL_RUN_STATES:
                    terminal = True
                    if run_state == RunState.timed_out:
                        raise TimeoutError(f"Runtime command timed out after {request.timeout_s}s")
                    result = RuntimeExecutionResult(
                        request=request, returncode=int(state.get("returncode") or 0),
                        stdout=str(state.get("stdout") or ""), stderr=str(state.get("stderr") or ""),
                        logs=list(request.log_lines), execution_backend="bridge",
                    )
                    if request.check and result.returncode != 0:
                        raise BridgeError(f"Runtime command failed with exit code {result.returncode}: {result.stderr}")
                    return result
                if time.monotonic() > deadline:
                    raise TimeoutError("Runtime bridge did not report a terminal result")
                time.sleep(self.poll_interval_s)
        finally:
            if submitted and not terminal:
                self._cancel_best_effort(run_id)


def resolve_gpu_enabled(gpu_preferred: bool, *, image: object | None = None) -> bool:
    """Preserve auto/cuda/cpu policy while resolving capability on the host."""
    if not gpu_preferred:
        return False
    mode = (os.environ.get("NEUROCADE_GPU_MODE") or "auto").strip().lower()
    if mode not in {"auto", "cuda", "cpu"}:
        raise ValueError("NEUROCADE_GPU_MODE must be auto, cuda, or cpu")
    if mode == "cpu":
        return False
    client = BridgeClient.from_environment()
    capability = client.resolve_capability(image) if image is not None else client.health()["gpu"]
    available = bool(capability.get("cuda", capability.get("available", False)))
    if available:
        return True
    if mode == "cuda":
        raise RuntimeGpuUnavailableError(f"CUDA was requested, but it is unavailable: {capability.get('reason', 'unknown reason')}")
    return False
