"""Stable workflow validation codes, independent of displayed wording."""

INPUT_ERRORS = {
    "pacs_provenance_invalid": "PACS input changed or lacks verified provenance; re-import it",
    "pacs_modality_incompatible": "PACS input modality is incompatible with this workflow",
    "pacs_dimensions_incompatible": "PACS input dimensions are incompatible with this workflow",
    "pacs_sequence_incompatible": "PACS input sequence is incompatible or unverified for this workflow",
}


class WorkflowInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(INPUT_ERRORS[code])


def workflow_error_code(error: Exception | str | None) -> str | None:
    if isinstance(error, WorkflowInputError):
        return error.code
    # Compatibility for already-persisted runs only; new failures store the code.
    return next((code for code, message in INPUT_ERRORS.items() if message == error), None)
