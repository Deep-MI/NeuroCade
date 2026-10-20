"""Contracts for rendering tool output into the in-app terminal.

Workflow logs are terminal transcripts. The browser shows them as text, so
anything a terminal would have consumed rather than drawn has to be resolved
here or it reaches the reader as unexplained noise.
"""

from __future__ import annotations

from pathlib import Path

from backend_common.run_logs import initialize_run_logs, render_run_logs

RUN_ID = "0f1c9d3a-0000-4000-8000-00000000abcd"


def _write_run(case_dir: Path, *, stdout: str = "", stderr: str = "") -> None:
    stdout_path, stderr_path = initialize_run_logs(case_dir, RUN_ID)
    stdout_path.write_bytes(stdout.encode("utf-8"))
    stderr_path.write_bytes(stderr.encode("utf-8"))


def test_colour_codes_do_not_reach_the_reader(tmp_path: Path) -> None:
    _write_run(tmp_path, stdout="\x1b[1;32mSegmentation complete\x1b[0m\n")

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert rendered == "Segmentation complete\n"


def test_backspaces_erase_the_text_they_overwrite(tmp_path: Path) -> None:
    """Keras-style progress redraws with backspaces, not carriage returns."""
    _write_run(tmp_path, stdout="predicting 1/1 - ETA: 0s" + "\b" * 9 + "- 967s/step\n")

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert rendered == "predicting 1/1 - 967s/step\n"
    assert "\b" not in rendered


def test_cursor_and_screen_control_sequences_are_removed(tmp_path: Path) -> None:
    _write_run(tmp_path, stdout="\x1b[?25l\x1b[2Kdownloading\x1b[?25h\n")

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert rendered == "downloading\n"


def test_window_title_sequences_are_removed(tmp_path: Path) -> None:
    _write_run(tmp_path, stdout="\x1b]0;recon-surf\x07running stage 3\n")

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert rendered == "running stage 3\n"


def test_no_control_characters_survive_rendering(tmp_path: Path) -> None:
    """Binary noise in a log must not become stray glyphs in the browser."""
    _write_run(tmp_path, stdout="start\x00\x07\x1f end\n", stderr="\x1b[31mboom\x1b[0m\n")

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert not [character for character in rendered if ord(character) < 32 and character not in "\n\t"]
    assert "start end" in rendered
    assert "boom" in rendered


def test_progress_bar_still_collapses_to_its_final_state(tmp_path: Path) -> None:
    """Carriage-return redraws keep collapsing; sanitizing must not undo that."""
    bar = "\r  0%|\x1b[32m    \x1b[0m| 0/320\r 53%|\x1b[32m██  \x1b[0m| 170/320\n"
    _write_run(tmp_path, stdout=bar)

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert rendered == "53%|██  | 170/320\n"


def test_plain_output_is_unchanged(tmp_path: Path) -> None:
    _write_run(tmp_path, stdout="line one\nline two\n", stderr="a warning\n")

    rendered = render_run_logs(tmp_path, RUN_ID)

    assert rendered == "line one\nline two\n--- STDERR ---\na warning\n"


def test_case_logs_identify_the_run_that_produced_the_transcript(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from typing import Any, cast

    from api_service.cases import queries

    from backend_common.db import RoleEnum, Run, RunStatus
    from backend_common.run_logs import initialize_run_logs

    run = Run(id="recorded-run", run_type="fastsurfer", status=RunStatus.completed,
              created_at=datetime(2026, 9, 22, tzinfo=UTC))
    stdout, _stderr = initialize_run_logs(tmp_path, run.id)
    stdout.write_text("finished\n")
    monkeypatch.setattr(queries, "get_case_for_user", lambda *_args: (SimpleNamespace(id="case"), None, RoleEnum.owner, tmp_path))
    monkeypatch.setattr(queries, "latest_case_run", lambda *_args: run)
    context = cast(Any, SimpleNamespace(user=SimpleNamespace(id="user")))
    response = queries.get_case_logs_for_user(cast(Any, None), context, case_id="case")
    assert response["logs"] == "finished\n"
    assert response["run"] == {"id": run.id, "tool_id": "fastsurfer", "status": "completed",
                               "created_at": "2026-09-22T00:00:00+00:00"}
    monkeypatch.setattr(queries, "latest_case_run", lambda *_args: None)
    assert queries.get_case_logs_for_user(cast(Any, None), context, case_id="case") == {"logs": "", "run": None}
