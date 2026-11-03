"""Input checks shared by approval preparation and workflow execution."""

from api_service.pacs.compatibility import validate_input


def validate_workflow_input(requirement, path, *, db=None, source_path=None):
    """Validate one resolved path against its catalog input declaration."""
    if requirement.kind == "directory":
        if not path.is_dir():
            raise ValueError("Workflow input must be an existing directory")
        return
    if not path.is_file():
        raise ValueError("Workflow input must be an existing regular file")
    validate_input(path, requirement, db=db, source_path=source_path)


def validate_workflow_inputs(workflow, paths, *, db=None):
    if len(paths) != len(workflow.inputs):
        raise ValueError(f"Tool {workflow.id!r} requires exactly {len(workflow.inputs)} ordered input file(s); received {len(paths)}.")
    for path, requirement in zip(paths, workflow.inputs, strict=True):
        validate_workflow_input(requirement, path, db=db)
