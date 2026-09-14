"""Input checks shared by approval preparation and workflow execution."""

from api_service.pacs.compatibility import validate_input


def validate_workflow_inputs(workflow, paths, *, db=None):
    if len(paths) != len(workflow.inputs):
        raise ValueError(f"Tool {workflow.id!r} requires exactly {len(workflow.inputs)} ordered input file(s); received {len(paths)}.")
    for path, requirement in zip(paths, workflow.inputs, strict=True):
        if not path.is_file():
            raise ValueError("Workflow input must be an existing regular file")
        validate_input(path, requirement, db=db)
