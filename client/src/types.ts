/* ------------------------------------------------------------------ */
/*  Shared type definitions for the NeuroCade frontend                 */
/* ------------------------------------------------------------------ */

export type LayerType = 'intensity' | 'segmentation' | 'drawing' | 'surface';
export type SurfaceColorMode = 'solid' | 'curvature' | 'annotation';

interface BaseViewerLayer {
  id: string;
  name: string;
  /** Original filename for reference / deduplication. */
  filename: string;
  artifactId?: string;
  url: string;
  opacity: number;
  colormap: string;
  visible: boolean;
  /** Whether this layer is shown in the 3D render pane. Defaults by layer type. */
  renderIn3D?: boolean;
  /** Whether this layer is shown as contours in 2D slice panes. Defaults by layer type. */
  renderInSlices?: boolean;
}

export interface IntensityVolumeLayer extends BaseViewerLayer {
  type?: 'intensity';
  /** Brightness adjustment, –100 to 100, default 0. */
  brightness?: number;
  /** Contrast multiplier, 0.0 to 2.0, default 1.0. */
  contrast?: number;
}

export interface SegmentationVolumeLayer extends BaseViewerLayer {
  type: 'segmentation';
  /** LUT variant used for segmentation rendering. 'binary' = 0:background / 1:structure. */
  lut?: 'freesurfer' | 'binary';
  /** Optional custom LUT file to use for segmentation rendering. */
  customLutUrl?: string;
  /** Brightness adjustment, –100 to 100, default 0. */
  brightness?: number;
  /** Contrast multiplier, 0.0 to 2.0, default 1.0. */
  contrast?: number;
}

export interface SurfaceLayer extends BaseViewerLayer {
  type: 'surface';
  /** Surface coloring source. */
  surfaceColorMode?: SurfaceColorMode;
  /** Row-major affine for the volume geometry this surface was reconstructed against. */
  surfaceReferenceAffine?: number[][];
  /** Optional FreeSurfer curvature file for surface vertex coloring. */
  curvatureUrl?: string;
  /** Optional FreeSurfer annotation file for parcellation vertex coloring. */
  annotationUrl?: string;
  /** Curvature magnitude that maps negative values to the bright gyri color. */
  curvatureNegativeThreshold?: number;
  /** Curvature magnitude that maps positive values to the dark sulci color. */
  curvaturePositiveThreshold?: number;
}

/** A loaded MRI viewer layer (volume or surface). */
export type Volume = IntensityVolumeLayer | SegmentationVolumeLayer | SurfaceLayer;

export function isSurfaceLayer(volume: Volume): volume is SurfaceLayer {
  return volume.type === 'surface';
}

export function isSegmentationLayer(volume: Volume): volume is SegmentationVolumeLayer {
  return volume.type === 'segmentation';
}

/** A case returned by the workspace case listing endpoint. */
export interface CaseSummary {
  case_id: string;
  subject_name: string;
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
  status: string;
  created_at: number;
  workspace_id?: string;
  thread_id?: string | null;
  artifact_count?: number;
}

/** Styling metadata for a run status badge. */
export interface StatusConfig {
  label: string;
  color: string;
  badge: string;
}

/** FastSurfer run parameters configured via the confirmation modal. */
export interface FastSurferParams {
  input_artifact_id: string;
  seg_only: boolean;
  no_bias: boolean;
  no_cereb: boolean;
  no_asegdkt: boolean;
  no_hypothal: boolean;
  three_t: boolean;
  vox_size?: string;
  case_name?: string;
}

/* ------------------------------------------------------------------ */
/*  API response types                                                 */
/* ------------------------------------------------------------------ */

export interface QueueStatus {
  total: number;
  active: number;
  queued: number;
}

export interface CaseListResponse {
  cases: CaseSummary[];
}

export interface StatusResponse {
  status: string;
}

export interface LogsResponse {
  logs: string;
}

export interface OutputVolume {
  id?: string;
  filename: string;
  downloadUrl: string;
  kind?: string;
  type?: LayerType;
  lut?: 'freesurfer' | 'binary';
  customLutDownloadUrl?: string;
  surfaceReferenceAffine?: number[][];
  curvatureDownloadUrl?: string;
  annotationDownloadUrl?: string;
  visible?: boolean;
}

export type SaveGeneratedVolumeResult = ArtifactListItem;

export interface OutputsListResponse {
  volumes: OutputVolume[];
}

export interface RunResult {
  task_id: string;
  case_id: string;
  status: string;
  workspace_id?: string | null;
}

export interface GuiStateSyncResponse {
  requested_cursor_position?: [number, number, number];
  requested_load_volume?: {
    download_path: string;
    filename: string;
    name: string;
    type: string;
    lut?: string;
    custom_lut_download_path?: string;
    curvature_download_path?: string;
    annotation_download_path?: string;
    visible?: boolean;
  };
  requested_close_volume?: { volume_id: string };
  requested_close_volumes?: { volume_id: string }[];
  requested_select_volumes?: { intensity_volume: string; segmentation_volume: string };
  requested_run_fastsurfer?: { case_id: string; input_artifact_id?: string; input_volume?: string; seg_only?: boolean; case_name?: string };
  requested_adjust_display?: { opacity?: number; brightness?: number; contrast?: number };
}

export interface ErrorResponse {
  error?: string;
  message?: string;
  detail?: string;
}

export interface SessionBootstrap {
  user: {
    id: string;
    email: string;
    full_name: string;
  };
  role: string;
  auth_mode: string;
  deployment_profile: 'local' | 'internal' | 'demo';
  public_url: string;
  features: Record<string, boolean>;
  limits: Record<string, number>;
  sample_data: {
    enabled?: boolean;
    scope?: 'per_user' | 'global';
    label?: string;
    provenance?: string;
    modifiable_copy?: boolean;
  };
  workspaces: WorkspaceSummary[];
  default_workspace_id: string | null;
  active_workspace_id: string | null;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  description?: string | null;
  role: string;
  kind: string;
  is_default: boolean;
  status: string;
  case_count?: number;
}

export interface MonitoringStatusItem {
  name: string;
  status: 'ok' | 'degraded' | 'down' | 'unknown';
  message?: string | null;
  details: Record<string, unknown>;
}

export interface MonitoringUserSummary {
  id: string;
  email: string;
  full_name: string;
  last_seen_at?: string | null;
}

export interface MonitoringEventSummary {
  id: string;
  source: string;
  level: string;
  event_type: string;
  message: string;
  user_id?: string | null;
  user_email?: string | null;
  method?: string | null;
  path?: string | null;
  status_code?: number | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface MonitoringAuditEventSummary {
  id: string;
  action: string;
  user_id?: string | null;
  user_email?: string | null;
  case_id?: string | null;
  artifact_id?: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface MonitoringSummary {
  generated_at: string;
  status: 'ok' | 'degraded' | 'down';
  active_window_minutes: number;
  totals: Record<string, number>;
  active_users: MonitoringUserSummary[];
  services: MonitoringStatusItem[];
  jobs: Record<string, unknown>;
  recent_errors: MonitoringEventSummary[];
  recent_activity: MonitoringAuditEventSummary[];
}

export interface MonitoringEventsResponse {
  events: MonitoringEventSummary[];
  audit_events: MonitoringAuditEventSummary[];
}

export interface WorkspaceBatchCaseRun {
  run_id: string;
  case_id: string;
  case_title: string;
  status: string;
  external_task_id?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceBatchRunSummary {
  run_id: string;
  workspace_id: string;
  status: string;
  run_type: string;
  execution_mode: 'workspace_wide' | 'per_case';
  command: string;
  report_name: string;
  analysis_id?: string | null;
  selected_case_count: number;
  total_cases: number;
  queued_cases: number;
  running_cases: number;
  completed_cases: number;
  failed_cases: number;
  canceled_cases: number;
  external_task_id?: string | null;
  artifact_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceBatchRunDetail extends WorkspaceBatchRunSummary {
  cases: WorkspaceBatchCaseRun[];
  artifacts: ArtifactListItem[];
}

export type AssistantScope = 'case' | 'workspace';

export interface UploadState {
  status: 'idle' | 'uploading' | 'uploaded' | 'failed';
  caseId: string | null;
}

interface PersistedBaseLayer {
  id: string;
  filename: string;
  name: string;
  url: string;
  visible: boolean;
  opacity: number;
  renderIn3D?: boolean;
  renderInSlices?: boolean;
}

export interface PersistedIntensityVolumeLayer extends PersistedBaseLayer {
  type: 'intensity';
  brightness: number;
  contrast: number;
}

export interface PersistedSegmentationVolumeLayer extends PersistedBaseLayer {
  type: 'segmentation';
  lut?: 'freesurfer' | 'binary';
  customLutUrl?: string;
  brightness: number;
  contrast: number;
}

export interface PersistedSurfaceLayer extends PersistedBaseLayer {
  type: 'surface';
  surfaceColorMode?: SurfaceColorMode;
  surfaceReferenceAffine?: number[][];
  curvatureUrl?: string;
  annotationUrl?: string;
  curvatureNegativeThreshold?: number;
  curvaturePositiveThreshold?: number;
}

/** Serialisable subset of Volume stored in localStorage. */
export type PersistedVolume = PersistedIntensityVolumeLayer | PersistedSegmentationVolumeLayer | PersistedSurfaceLayer;

/** Per-case persistence envelope stored in localStorage. */
export interface CaseState {
  caseId: string;
  volumes: PersistedVolume[];
  lastAccessed: number;
}

/* ------------------------------------------------------------------ */
/*  Chat types                                                         */
/* ------------------------------------------------------------------ */

export interface ChatImagePart {
  type: 'image_url';
  image_url: { url: string };
}

export interface ChatTextPart {
  type: 'text';
  text: string;
}

export type ChatContentPart = ChatTextPart | ChatImagePart;

export interface ToolCallEntry {
  name: string;
  arguments: Record<string, unknown>;
  result: string;
}

export interface ReasoningEntry {
  summary: string;
  round?: number;
  tool_names?: string[];
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'info' | 'tool-calls';
  content: string | ChatContentPart[];
  toolCalls?: ToolCallEntry[];
  reasoningEntries?: ReasoningEntry[];
}

export interface AssistantHistoryResponse {
  thread_id: string | null;
  messages: ChatMessage[];
}

export interface ProviderSummary {
  provider: string;
  provider_family: string;
  model: string;
  role: string;
  is_default: boolean;
  native_tool_calling: boolean;
  json_mode: boolean;
  vision: boolean;
  streaming: boolean;
  available: boolean;
  availability_reason?: string | null;
}

export interface ArtifactListItem {
  id?: string;
  case_id?: string | null;
  workspace_id?: string | null;
  name: string;
  kind: string;
  downloadPath: string;
  metadata: Record<string, unknown>;
}

export interface LocationInfo {
  vox: [number, number, number];
  labelIndex: number;
  labelName: string;
  labelColor?: [number, number, number];
}

export interface MriSnapshots {
  sagittal: string;
  coronal: string;
  axial: string;
}

export interface MriViewerRef {
  getSnapshots: () => MriSnapshots | null;
}
