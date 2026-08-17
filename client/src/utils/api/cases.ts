import type {
  AnalysisToolSummary,
  ArtifactListItem,
  CaseListResponse,
  CaseSummary,
  OutputsListResponse,
  QueueStatus,
  RunResult,
  StatusResponse,
} from '../../types';

import { appFetch, appFetchUrl, appJson, appOk, appUrl, expectOk, jsonRequest } from './core';
import { configuredOutputLayerType } from '../artifactOutputs';

interface CaseRunItem {
  status: string;
  run_type: string;
}

interface ApiArtifactListItem {
  id: string;
  case_id?: string | null;
  workspace_id?: string | null;
  name: string;
  kind: string;
  download_path: string;
  metadata: Record<string, unknown>;
}

interface StartRunPayload {
  tool_id: string;
  case_id: string;
  input_artifact_ids: string[];
  output_name_overrides: Record<string, string>;
}

export interface CaseMetadataInput {
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
}

interface CaseUpdateInput extends CaseMetadataInput {
  title?: string;
}

export async function fetchCases(workspaceId?: string | null): Promise<CaseListResponse> {
  const params = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  const cases = await appJson<CaseSummary[]>(`/cases${params}`, 'Failed to fetch cases');
  return { cases };
}

export async function fetchAnalysisTools(): Promise<AnalysisToolSummary[]> {
  return appJson<AnalysisToolSummary[]>('/analysis-tools', 'Failed to fetch analysis tools');
}

export async function fetchStatus(caseId: string): Promise<StatusResponse> {
  const runs = await appJson<CaseRunItem[]>(`/cases/${caseId}/runs`, 'Failed to fetch case status');
  const latestRun = runs[0];
  return {
    status: latestRun?.status ?? 'uploaded',
    workflowId: latestRun?.run_type,
  };
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

function normalizeArtifactListItem(artifact: ApiArtifactListItem): ArtifactListItem {
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
      .filter((artifact) => {
        const outputType = artifact.metadata?.output_type;
        const configuredLayerType = configuredOutputLayerType(outputType);
        return configuredLayerType !== null && (
          configuredLayerType !== undefined
          || (outputType === undefined && (
            artifact.kind === 'volume'
            || artifact.metadata?.layer_role === 'surface'
          ))
        );
      })
      .map((artifact) => {
        const configuredOutputType = artifact.metadata?.output_type;
        const configuredLayerType = configuredOutputLayerType(configuredOutputType);
        const outputType = configuredOutputType === 'intensity_volume'
          || configuredOutputType === 'segmentation_volume'
          || configuredOutputType === 'surface'
          ? configuredOutputType
          : undefined;
        return {
          id: artifact.id,
          name: typeof artifact.metadata?.display_name === 'string'
            ? artifact.metadata.display_name
            : undefined,
          filename: artifact.name,
          downloadUrl: appUrl(artifact.downloadPath),
          kind: artifact.kind,
          outputType,
          type: configuredLayerType ?? (artifact.metadata?.layer_role === 'surface'
            ? 'surface'
            : artifact.metadata?.volume_role === 'segmentation' ? 'segmentation' : 'intensity'),
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
        };
      }),
  };
}

export async function fetchCaseArtifacts(caseId: string): Promise<ArtifactListItem[]> {
  const artifacts = await appJson<ApiArtifactListItem[]>(
    `/cases/${encodeURIComponent(caseId)}/artifacts`,
    'Failed to fetch case artifacts',
  );
  return artifacts.map(normalizeArtifactListItem);
}

export async function saveGeneratedVolume(
  caseId: string,
  params: {
    filename: string;
    blob: Blob;
    metadata?: Record<string, unknown>;
  },
): Promise<ArtifactListItem> {
  const formData = new FormData();
  formData.append('filename', params.filename);
  formData.append('metadata', JSON.stringify(params.metadata ?? {}));
  formData.append('file', params.blob, params.filename);
  const response = await appFetch(`/cases/${encodeURIComponent(caseId)}/generated-volume`, {
    method: 'POST',
    body: formData,
  });
  await expectOk(response, 'Failed to save generated volume');
  return normalizeArtifactListItem(await response.json() as ApiArtifactListItem);
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

export async function startRun(requestBody: StartRunPayload): Promise<RunResult> {
  const data = await appJson<{ id: string; case_id: string; status: string; workspace_id: string }>(
    '/runs',
    'Failed to start analysis workflow',
    jsonRequest(requestBody, { method: 'POST' }),
  );
  return {
    run_id: data.id,
    case_id: data.case_id,
    status: data.status,
    workspace_id: data.workspace_id,
  };
}

export async function createCaseWithUpload(
  files: File | File[],
  workspaceId: string,
  caseName?: string,
  metadata: CaseMetadataInput = {},
): Promise<{ case_id: string; filenames: string[]; workspace_id: string; title: string }> {
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
  return appJson<{ case_id: string; filenames: string[]; workspace_id: string; title: string }>(
    '/cases',
    'Upload failed',
    { method: 'POST', body: formData },
  );
}

export async function addUploadToCase(
  files: File | File[],
  caseId: string,
): Promise<{ case_id: string; filenames: string[]; workspace_id: string; title: string }> {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];
  if (uploadFiles.length === 1) {
    formData.append('file', uploadFiles[0]);
  } else {
    uploadFiles.forEach((file) => formData.append('files', file));
  }
  return appJson<{ case_id: string; filenames: string[]; workspace_id: string; title: string }>(
    `/cases/${encodeURIComponent(caseId)}/uploads`,
    'Upload failed',
    { method: 'POST', body: formData },
  );
}

export async function cancelCaseRun(caseId: string): Promise<void> {
  await appOk(`/cases/${encodeURIComponent(caseId)}/cancel`, 'Cancel failed', { method: 'POST' });
}

export async function updateCase(
  caseId: string,
  updates: CaseUpdateInput,
): Promise<{
  id: string;
  title: string;
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
}> {
  return appJson<{
    id: string;
    title: string;
    description?: string | null;
    modalities?: string[];
    tags?: string[];
    notes?: string | null;
  }>(
    `/cases/${encodeURIComponent(caseId)}`,
    'Case update failed',
    jsonRequest(updates, { method: 'PATCH' }),
  );
}

export async function deleteCase(caseId: string): Promise<void> {
  await appOk(`/cases/${encodeURIComponent(caseId)}`, 'Delete failed', { method: 'DELETE' });
}
