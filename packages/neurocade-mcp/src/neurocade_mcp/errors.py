"""Allowlisted diagnostics: actionable errors without reflecting credentials."""

class TransferError(ValueError):
    def __init__(self, code, message, *, status=None, retryable=False):
        super().__init__(message)
        self.details = {"code": code, "message": message, "retryable": retryable}
        if status is not None:
            self.details["http_status"] = status


TITLE_MESSAGE = "title must be a lowercase slug, 2-64 characters, using a-z, 0-9 and hyphens, starting and ending with a letter or digit (for example claude-case). Correct the title; do not modify the scan."

KNOWN_DETAILS = {
    "Specify title for a new case OR case_id for an existing case, not both.": ("INVALID_PARAMETERS", "Specify title for a new case OR case_id for an existing case, not both."),
    "Case name must be a lowercase slug, 2-64 characters, using only a-z, 0-9, and hyphen": ("INVALID_CASE_TITLE", TITLE_MESSAGE),
    "Case name cannot be empty": ("INVALID_CASE_TITLE", TITLE_MESSAGE),
    "Filename must not contain directories": ("INVALID_FILENAME", "filename must be a file name without directories."),
    "Empty upload": ("EMPTY_UPLOAD", "The uploaded file is empty. Select a nonempty scan."),
    "SHA-256 mismatch": ("CHECKSUM_MISMATCH", "Uploaded bytes do not match the supplied SHA-256. Retry the original file with its checksum; do not rewrite its imaging header."),
    "Empty upload or SHA-256 mismatch": ("UPLOAD_BYTES_INVALID", "Upload was empty or its bytes did not match SHA-256."),
    "Installation identity mismatch; reconnect NeuroCade": ("INSTALLATION_MISMATCH", "The connection belongs to another installation. Reconnect to the intended NeuroCade app."),
    "IDEMPOTENCY_CONFLICT: use the original upload parameters": ("IDEMPOTENCY_CONFLICT", "This upload key was already used with different parameters. Inspect the previous upload. Reuse its original parameters for a retry; use a new key only for a corrected or new request."),
    "Upload is incomplete or invalidated; inspect the case before retrying with a new key": ("UPLOAD_OUTCOME_UNKNOWN", "A previous upload with this key has an uncertain outcome. Inspect the case before creating another upload."),
}

# These backend diagnostics are constants, not user-supplied strings or URLs.
for _message in (
    "Read-only connection cannot upload", "Uploads are disabled for this deployment",
    "This action is disabled for this deployment", "Case has an active PACS import",
    "Cannot modify a case while processing may still own its outputs",
    "Uploaded MRI file could not be read", "Compressed MRI uploads must be gzip-encoded",
    "NIfTI upload is too small to contain a valid header", "NIfTI upload has an invalid header",
    "DICOM ZIP upload is not a valid ZIP archive", "DICOM upload could not be read",
    "DICOM upload is missing the DICM header", "No upload file provided",
    "DICOM ZIP contains too many files", "DICOM ZIP expanded size exceeds the configured limit",
    "DICOM ZIP contains unsafe paths", "DICOM conversion produced no NIfTI volume",
    "Upload either one MRI volume or a DICOM series, not both",
    "Upload exceeds the configured file size limit",
):
    KNOWN_DETAILS[_message] = ("REQUEST_REJECTED", _message)
