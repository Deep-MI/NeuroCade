export class ApiError extends Error {
  constructor(message: string, readonly code?: string) { super(message); this.name = 'ApiError'; }
}

export function decodeApiError(body: unknown, fallback: string): ApiError {
  if (!body || typeof body !== 'object') return new ApiError(fallback);
  const record = body as Record<string, unknown>;
  const detail = record.detail ?? record.error ?? record.message;
  if (typeof detail === 'string') return new ApiError(detail);
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return new ApiError(detail.message, 'code' in detail && typeof detail.code === 'string' ? detail.code : undefined);
  }
  return new ApiError(fallback);
}

const pacsMessages: Record<string, string> = {
  connection_failed: 'Cannot reach PACS. Check the connection, then retry.',
  authentication_failed: 'PACS authentication failed. Ask your administrator to check the connection credentials.',
  retrieval_failed: 'PACS could not return the requested images. Check availability, then retry.',
  process_interrupted: 'The import was interrupted. Retry unfinished series.',
  processing_interrupted: 'The import was interrupted. Retry unfinished series.',
  cleanup_failed: 'Temporary image cleanup failed. Ask your administrator to resolve it before retrying.',
  access_revoked: 'Your access changed. Ask the workspace owner to check your permissions.',
  conversion_failed: 'Image conversion failed. Check that this series is supported before retrying.',
  canceled: 'Import canceled. You can retry unfinished series.',
  incompatible_sequence: 'This series conflicts with native-T1 verification. Re-import without that verification for viewing.',
  disk_space_limit: 'There is not enough free storage. Free space before retrying.',
  conversion_disk_limit: 'Conversion exceeded the storage limit. Ask your administrator to review the import limits.',
  output_size_limit: 'Converted images exceed the configured size limit.',
  import_size_limit: 'The import exceeds the configured size limit. Select fewer series.',
  instance_size_limit: 'An image exceeds the configured size limit.',
  instance_limit: 'The series contains too many images for the configured import limit.',
  series_limit: 'The study contains too many series for the configured import limit.',
  metadata_limit: 'PACS returned more metadata than the configured limit allows.',
  study_not_found: 'The study is no longer available. Search again.',
  empty_series: 'This series has no available images. Select another series.',
  no_volume: 'Conversion produced no usable volume. Select a supported image series.',
  identity_mismatch: 'Image identity does not match the selected study. Import stopped; ask your PACS administrator to investigate.',
  source_changed: 'The PACS source changed. Search again and create a new import.',
  series_changed: 'The source series changed. Search again and create a new import.',
  truncated_instance: 'An image transfer was incomplete. Check the connection and retry.',
  unexpected_instance: 'PACS returned an unexpected image. Import stopped for safety.',
  unsupported_sop_class: 'This DICOM object type is not supported for conversion.',
  unsupported_media_type: 'PACS returned an unsupported image format.',
  invalid_uid: 'The DICOM identifier is invalid. Ask your PACS administrator to check the study.',
  invalid_metadata: 'PACS returned invalid metadata. Ask your PACS administrator to check the study.',
  invalid_instance_manifest: 'The image list is inconsistent. Search again or ask your PACS administrator.',
  invalid_series_manifest: 'The series list is inconsistent. Search again or ask your PACS administrator.',
  invalid_volume: 'The converted volume failed validation and was not published.',
  invalid_sidecar: 'Conversion metadata failed validation and was not published.',
};

export function pacsErrorMessage(code: string): string {
  return pacsMessages[code] ?? 'PACS operation failed. Try again or contact your administrator.';
}

export function analysisFailureMessage(message?: string | null, code?: string | null): string {
  const guidance: Record<string, string> = {
    pacs_sequence_incompatible: 'Choose a native, noncontrast structural T1 series verified during import. T2, FLAIR, and postcontrast scans can still be imported and viewed.',
    pacs_provenance_invalid: 'Re-import this series from PACS before running analysis.',
    pacs_modality_incompatible: 'Choose an input modality supported by this workflow.',
    pacs_dimensions_incompatible: 'Choose an input with the dimensions required by this workflow.',
  };
  const detail = code ? guidance[code] : undefined;
  return `${message ? `Analysis could not complete: ${message}` : 'Analysis job failed. No further details were reported.'}${detail ? `\n${detail}` : ''}`;
}
