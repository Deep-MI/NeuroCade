import type {
  ArtifactListItem,
  CaseListResponse,
  FastSurferParams,
  OutputsListResponse,
  QueueStatus,
  RunResult,
  StatusResponse,
} from '../../types';

import { appFetch, appFetchUrl, appJson, appOk, appUrl, expectOk, jsonRequest } from './core';

interface CaseListItem {
  id: string;
  title: string;
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
  latest_run_status?: string | null;
  created_at: string;
  workspace_id: string;
  thread_id?: string | null;
  artifact_count?: number;
}

interface CaseRunItem {
  status: string;
}

export interface ApiArtifactListItem {
  id?: string;
  case_id?: string | null;
  workspace_id?: string | null;
  name: string;
  kind: string;
  download_path: string;
  metadata: Record<string, unknown>;
}

interface StartRunPayload {
  workspace_id: string | null;
  case_id: string | null;
  source_case_id: string | null;
  input_artifact_id: string;
  seg_only: boolean;
  surf_only: boolean;
  no_bias: boolean;
  no_cereb: boolean;
  no_asegdkt: boolean;
  no_hypothal: boolean;
  three_t: boolean;
  vox_size: string;
  case_name?: string;
}

export interface CaseMetadataInput {
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
}

export async function fetchCases(workspaceId?: string | null): Promise<CaseListResponse> {
  const params = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  const cases = await appJson<CaseListItem[]>(`/cases${params}`, 'Failed to fetch cases');
  return {
    cases: cases.map((caseItem) => ({
      case_id: caseItem.id,
      subject_name: caseItem.title,
      description: caseItem.description ?? null,
      modalities: Array.isArray(caseItem.modalities) ? caseItem.modalities : [],
      tags: Array.isArray(caseItem.tags) ? caseItem.tags : [],
      notes: caseItem.notes ?? null,
      status: caseItem.latest_run_status ?? 'uploaded',
      created_at: Date.parse(caseItem.created_at) / 1000,
      workspace_id: caseItem.workspace_id,
      thread_id: caseItem.thread_id ?? null,
      artifact_count: caseItem.artifact_count ?? 0,
    })),
  };
}

export async function fetchStatus(caseId: string): Promise<StatusResponse> {
  const runs = await appJson<CaseRunItem[]>(`/cases/${caseId}/runs`, 'Failed to fetch case status');
  return { status: runs[0]?.status ?? 'uploaded' };
}

export async function fetchLogs(caseId: string): Promise<string> {
  const res = await appFetch(`/cases/${caseId}/logs`);
  if (!res.ok) {
    console.warn(`fetchLogs: server returned ${res.status} for case ${caseId}`);
    return '';
  }
  const data = await res.json() as { logs: string };
  return data.logs;
}

interface SurfaceCompanionUrls {
  curvature?: string;
  annotation?: string;
}

export function normalizeArtifactListItem(artifact: ApiArtifactListItem): ArtifactListItem {
  return {
    id: artifact.id,
    case_id: artifact.case_id,
    workspace_id: artifact.workspace_id,
    name: artifact.name,
    kind: artifact.kind,
    downloadPath: artifact.download_path,
    metadata: artifact.metadata,
  };
}

function collectSurfaceCompanionUrls(artifacts: ArtifactListItem[]): Map<string, SurfaceCompanionUrls> {
  const companionsByHemisphere = new Map<string, SurfaceCompanionUrls>();
  for (const artifact of artifacts) {
    const hemisphere = typeof artifact.metadata?.hemisphere === 'string' ? artifact.metadata.hemisphere : undefined;
    if (!hemisphere) continue;
    const companions = companionsByHemisphere.get(hemisphere) ?? {};
    if (artifact.metadata?.layer_role === 'surface-curvature') {
      companions.curvature = appUrl(artifact.downloadPath);
    } else if (artifact.metadata?.layer_role === 'surface-annotation') {
      companions.annotation = appUrl(artifact.downloadPath);
    }
    companionsByHemisphere.set(hemisphere, companions);
  }
  return companionsByHemisphere;
}

export async function fetchOutputsList(caseId: string): Promise<OutputsListResponse> {
  const res = await appFetch(`/cases/${caseId}/artifacts`);
  if (!res.ok) {
    console.warn(`fetchOutputsList: server returned ${res.status} for case ${caseId}`);
    return { volumes: [] };
  }
  const artifacts = (await res.json() as ApiArtifactListItem[]).map(normalizeArtifactListItem);
  const companionUrlsByHemisphere = collectSurfaceCompanionUrls(artifacts);
  return {
    volumes: artifacts
      .filter((artifact) => (
        artifact.kind === 'volume'
        || artifact.metadata?.layer_role === 'surface'
      ))
      .map((artifact) => ({
        id: artifact.id,
        filename: artifact.name,
        downloadUrl: appUrl(artifact.downloadPath),
        kind: artifact.kind,
        type: artifact.metadata?.layer_role === 'surface'
          ? 'surface'
          : artifact.metadata?.volume_role === 'segmentation' ? 'segmentation' : 'intensity',
        lut: typeof artifact.metadata?.lut === 'string'
          ? artifact.metadata.lut as 'freesurfer' | 'binary'
          : undefined,
        customLutDownloadUrl: typeof artifact.metadata?.custom_lut_url === 'string'
          ? appUrl(artifact.metadata.custom_lut_url)
          : undefined,
        curvatureDownloadUrl: typeof artifact.metadata?.curvature_url === 'string'
          ? appUrl(artifact.metadata.curvature_url)
          : typeof artifact.metadata?.hemisphere === 'string'
            ? companionUrlsByHemisphere.get(artifact.metadata.hemisphere)?.curvature
            : undefined,
        annotationDownloadUrl: typeof artifact.metadata?.annotation_url === 'string'
          ? appUrl(artifact.metadata.annotation_url)
          : typeof artifact.metadata?.hemisphere === 'string'
            ? companionUrlsByHemisphere.get(artifact.metadata.hemisphere)?.annotation
            : undefined,
        visible: typeof artifact.metadata?.visible === 'boolean'
          ? artifact.metadata.visible
          : undefined,
      })),
  };
}

export async function fetchCaseArtifacts(caseId: string): Promise<ArtifactListItem[]> {
  const artifacts = await appJson<ApiArtifactListItem[]>(
    `/cases/${encodeURIComponent(caseId)}/artifacts`,
    'Failed to fetch case artifacts',
  );
  return artifacts.map(normalizeArtifactListItem);
}

function filenameFromContentDisposition(headerValue: string | null, fallback: string): string {
  if (!headerValue) {
    return fallback;
  }
  const encodedMatch = /filename\*=UTF-8''([^;]+)/i.exec(headerValue);
  if (encodedMatch?.[1]) {
    try {
      return decodeURIComponent(encodedMatch[1]);
    } catch {
      return fallback;
    }
  }
  const plainMatch = /filename="?([^"]+)"?/i.exec(headerValue);
  return plainMatch?.[1] ?? fallback;
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

async function downloadResponse(response: Response, fallbackFilename: string, fallbackError: string): Promise<void> {
  await expectOk(response, fallbackError);
  const blob = await response.blob();
  const filename = filenameFromContentDisposition(response.headers.get('content-disposition'), fallbackFilename);
  triggerBrowserDownload(blob, filename);
}

export async function downloadArtifactFile(artifact: ArtifactListItem): Promise<void> {
  const response = await appFetchUrl(appUrl(artifact.downloadPath));
  await downloadResponse(response, artifact.name, 'Failed to download artifact');
}

export async function downloadCaseArchive(caseId: string, caseTitle?: string | null): Promise<void> {
  const response = await appFetch(`/cases/${encodeURIComponent(caseId)}/download`);
  await downloadResponse(response, `${caseTitle ?? caseId}.zip`, 'Failed to download case archive');
}

export async function fetchQueueStatus(workspaceId: string): Promise<QueueStatus> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  return appJson<QueueStatus>(`/queue-status?${params.toString()}`, 'Failed to fetch queue status');
}

export async function startRun(formData: FormData): Promise<RunResult> {
  const workspaceIdValue = formData.get('workspace_id');
  const caseIdValue = formData.get('case_id');
  const sourceCaseIdValue = formData.get('source_case_id');
  const caseNameValue = formData.get('case_name');

  const workspaceId = typeof workspaceIdValue === 'string' ? workspaceIdValue : null;
  const caseId = typeof caseIdValue === 'string' ? caseIdValue : null;
  const sourceCaseId = typeof sourceCaseIdValue === 'string' ? sourceCaseIdValue : null;
  const caseName = typeof caseNameValue === 'string' ? caseNameValue : undefined;
  const inputArtifactIdValue = formData.get('input_artifact_id');
  const inputArtifactId = typeof inputArtifactIdValue === 'string' ? inputArtifactIdValue : '';

  const requestBody: StartRunPayload = {
    workspace_id: workspaceId,
    case_id: sourceCaseId ? null : caseId,
    source_case_id: sourceCaseId,
    input_artifact_id: inputArtifactId,
    seg_only: formData.get('seg_only') === 'true',
    surf_only: formData.get('surf_only') === 'true',
    no_bias: formData.get('no_bias') === 'true',
    no_cereb: formData.get('no_cereb') === 'true',
    no_asegdkt: formData.get('no_asegdkt') === 'true',
    no_hypothal: formData.get('no_hypothal') === 'true',
    three_t: formData.get('three_t') === 'true',
    vox_size: typeof formData.get('vox_size') === 'string' ? (formData.get('vox_size') as string) : 'min',
    case_name: caseName,
  };
  const data = await appJson<{ id: string; case_id?: string | null; status: string; workspace_id?: string | null }>(
    '/runs',
    'Failed to start FastSurfer run',
    jsonRequest(requestBody, { method: 'POST' }),
  );
  return {
    task_id: data.id,
    case_id: data.case_id ?? caseId ?? '',
    status: data.status,
    workspace_id: data.workspace_id ?? null,
  };
}

export async function createCaseWithUpload(
  files: File | File[],
  workspaceId: string,
  caseName?: string,
  metadata: CaseMetadataInput = {},
): Promise<{ case_id: string; filename: string; workspace_id: string; title: string }> {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];
  formData.append('workspace_id', workspaceId);
  if (caseName) {
    formData.append('title', caseName);
  }
  if (metadata.description) {
    formData.append('description', metadata.description);
  }
  if (metadata.modalities && metadata.modalities.length > 0) {
    formData.append('modalities', JSON.stringify(metadata.modalities));
  }
  if (metadata.tags && metadata.tags.length > 0) {
    formData.append('tags', JSON.stringify(metadata.tags));
  }
  if (metadata.notes) {
    formData.append('notes', metadata.notes);
  }
  if (uploadFiles.length === 1) {
    formData.append('file', uploadFiles[0]);
  } else {
    uploadFiles.forEach((file) => formData.append('files', file));
  }
  const data = await appJson<{ case_id: string; filename: string; workspace_id: string; title: string }>(
    '/cases',
    'Upload failed',
    { method: 'POST', body: formData },
  );
  return { case_id: data.case_id, filename: data.filename, workspace_id: data.workspace_id, title: data.title };
}

export async function addUploadToCase(
  files: File | File[],
  caseId: string,
): Promise<{ case_id: string; filename: string; workspace_id: string; title: string }> {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];
  if (uploadFiles.length === 1) {
    formData.append('file', uploadFiles[0]);
  } else {
    uploadFiles.forEach((file) => formData.append('files', file));
  }
  const data = await appJson<{ case_id: string; filename: string; workspace_id: string; title: string }>(
    `/cases/${encodeURIComponent(caseId)}/uploads`,
    'Upload failed',
    { method: 'POST', body: formData },
  );
  return { case_id: data.case_id, filename: data.filename, workspace_id: data.workspace_id, title: data.title };
}

export async function cancelCaseRun(caseId: string): Promise<void> {
  await appOk(`/cases/${encodeURIComponent(caseId)}/cancel`, 'Cancel failed', { method: 'POST' });
}

export async function renameCase(
  oldId: string,
  newId: string,
  metadata: CaseMetadataInput = {},
): Promise<{
  old_id: string;
  new_id: string;
  title: string;
  case_id: string;
  old_title: string;
  new_title: string;
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
}> {
  return appJson<{
    old_id: string;
    new_id: string;
    title: string;
    case_id: string;
    old_title: string;
    new_title: string;
    description?: string | null;
    modalities?: string[];
    tags?: string[];
    notes?: string | null;
  }>(
    `/cases/${encodeURIComponent(oldId)}`,
    'Rename failed',
    jsonRequest({ title: newId, ...metadata }, { method: 'PATCH' }),
  );
}

export async function deleteCase(caseId: string): Promise<void> {
  await appOk(`/cases/${encodeURIComponent(caseId)}`, 'Delete failed', { method: 'DELETE' });
}

export function buildRunFormData(
  params: FastSurferParams,
  opts: {
    activeCaseId?: string | null;
    currentCaseName?: string | null;
    workspaceId?: string | null;
  } = {},
): FormData {
  const normalizeCaseAlias = (value?: string | null): string | null => {
    if (!value) return null;
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (trimmed.toLowerCase().endsWith('.nii.gz')) {
      return trimmed.slice(0, -7);
    }
    if (trimmed.toLowerCase().endsWith('.nii') || trimmed.toLowerCase().endsWith('.mgz')) {
      return trimmed.replace(/\.(nii|mgz)$/i, '');
    }
    return trimmed;
  };

  const formData = new FormData();
  const effectiveCaseId = opts.activeCaseId ?? null;
  const requestedCaseName = normalizeCaseAlias(params.case_name);
  const existingAliases = new Set(
    [
      normalizeCaseAlias(effectiveCaseId),
      normalizeCaseAlias(opts.currentCaseName),
    ].filter((value): value is string => Boolean(value))
  );
  const isRename = Boolean(effectiveCaseId && requestedCaseName && !existingAliases.has(requestedCaseName));
  if (opts.workspaceId) formData.append('workspace_id', opts.workspaceId);

  const noCereb = params.no_bias || params.no_cereb;
  formData.append('seg_only', String(params.seg_only));
  formData.append('no_bias', String(params.no_bias));
  formData.append('no_cereb', String(noCereb));
  formData.append('no_asegdkt', String(params.no_asegdkt));
  formData.append('no_hypothal', String(params.no_hypothal));
  formData.append('three_t', String(params.three_t));
  if (params.vox_size) formData.append('vox_size', params.vox_size);
  if (requestedCaseName) formData.append('case_name', requestedCaseName);
  formData.append('input_artifact_id', params.input_artifact_id);

  if (effectiveCaseId && isRename) {
    formData.append('source_case_id', effectiveCaseId);
  } else if (effectiveCaseId) {
    formData.append('case_id', effectiveCaseId);
  }

  return formData;
}
