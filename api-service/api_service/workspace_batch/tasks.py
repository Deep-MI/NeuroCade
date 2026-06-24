"""Workspace batch job entry points registered with the in-process JobWorker."""

from api_service.jobs import job_manager
from api_service.workspace_batch.runner import process_workspace_batch_case, process_workspace_command_run

EXECUTE_WORKSPACE_BATCH_CASE_TASK = "api_service.workspace_batch.execute_workspace_batch_case_task"
EXECUTE_WORKSPACE_COMMAND_TASK = "api_service.workspace_batch.execute_workspace_command_task"


@job_manager.task(EXECUTE_WORKSPACE_BATCH_CASE_TASK)
def execute_workspace_batch_case_task(run_id: str, case_id: str, task_id: str, is_probe: bool = False) -> None:
    """Process one case in a workspace batch run."""
    process_workspace_batch_case(run_id, case_id, task_id=task_id, is_probe=is_probe)


@job_manager.task(EXECUTE_WORKSPACE_COMMAND_TASK)
def execute_workspace_command_task(run_id: str, task_id: str) -> None:
    """Run the command associated with a workspace run."""
    process_workspace_command_run(run_id, task_id=task_id)
