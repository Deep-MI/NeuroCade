"""Celery task entry points for workspace batch processing."""

from api_service.celery_app import celery_app
from api_service.workspace_batch.runner import process_workspace_batch_case, process_workspace_command_run


@celery_app.task(bind=True, name="api_service.workspace_batch.execute_workspace_batch_case_task")
def execute_workspace_batch_case_task(self, run_id: str, case_id: str, is_probe: bool = False) -> None:
    """Process one case in a workspace batch run.

    Parameters
    ----------
    run_id : str
        Workspace batch run identifier.
    case_id : str
        Identifier of the case to process.
    is_probe : bool
        Whether the case is being processed as a probe run.

    Returns
    -------
    None
        This function does not return a value.
    """
    process_workspace_batch_case(
        run_id,
        case_id,
        task_id=str(self.request.id or ""),
        is_probe=is_probe,
    )


@celery_app.task(bind=True, name="api_service.workspace_batch.execute_workspace_command_task")
def execute_workspace_command_task(self, run_id: str) -> None:
    """Run the command associated with a workspace run.

    Parameters
    ----------
    run_id : str
        Workspace run identifier.

    Returns
    -------
    None
        This function does not return a value.
    """
    process_workspace_command_run(
        run_id,
        task_id=str(self.request.id or ""),
    )
