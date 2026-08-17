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
}

export interface IntensityVolumeLayer extends BaseViewerLayer {
  type: 'intensity';
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
  /** Optional FreeSurfer curvature file for surface vertex coloring. */
  curvatureUrl?: string;
  /** Optional FreeSurfer annotation file for parcellation vertex coloring. */
  annotationUrl?: string;
  /** Absolute negative-curvature endpoint mapped to bright green. */
  curvatureNegativeThreshold?: number;
  /** Positive-curvature endpoint mapped to bright red. */
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
  id: string;
  title: string;
  description?: string | null;
  modalities?: string[];
  tags?: string[];
  notes?: string | null;
  latest_run_status: string | null;
  created_at: string;
  workspace_id: string;
  thread_id?: string | null;
  artifact_count?: number;
}

/** Styling metadata for a run status badge. */
export interface StatusConfig {
  label: string;
  color: string;
  badge: string;
}

/** One ordered input declared by a configured analysis workflow. */
interface AnalysisToolInput {
  name: string;
  description: string;
}

/** One typed output declared by a configured analysis workflow. */
interface AnalysisToolOutput {
  name: string;
  type: 'intensity_volume' | 'segmentation_volume' | 'surface' | 'other';
  path: string;
  description: string;
  required: boolean;
}

/** Parameters submitted by the generated Run Analysis form. */
export interface AnalysisRunParams {
  tool_id: string;
  input_artifact_ids: string[];
  output_name_overrides: Record<string, string>;
}

/* ------------------------------------------------------------------ */
/*  API response types                                                 */
/* ------------------------------------------------------------------ */

export interface QueueStatus {
  total: number;
  active: number;
  queued: number;
}

export interface AnalysisToolSummary {
  id: string;
  label: string;
  description: string;
  inputs: AnalysisToolInput[];
  outputs: AnalysisToolOutput[];
  input_artifact_kind: 'intensity_volume';
  execution: {
    mode: 'synchronous' | 'background';
    gpu: boolean;
    timeout_s: number | null;
    queue: string;
  };
}

export interface CaseListResponse {
  cases: CaseSummary[];
}

export interface StatusResponse {
  status: string;
}

export interface OutputVolume {
  id: string;
  name?: string;
  filename: string;
  downloadUrl: string;
  kind: string;
  outputType?: 'intensity_volume' | 'segmentation_volume' | 'surface';
  type: Exclude<LayerType, 'drawing'>;
  lut?: 'freesurfer' | 'binary';
  customLutDownloadUrl?: string;
  curvatureDownloadUrl?: string;
  annotationDownloadUrl?: string;
  visible?: boolean;
}

export interface OutputsListResponse {
  volumes: OutputVolume[];
}

export interface RunResult {
  run_id: string;
  case_id: string;
  status: string;
  workspace_id: string;
}

export interface GuiStateSyncResponse {
  commands: GuiCommand[];
}

export interface GuiLayerState {
  id: string;
  artifact_id?: string;
  filename: string;
  name: string;
  type: Exclude<LayerType, 'drawing'>;
  role?: string;
  hemisphere?: 'left' | 'right';
  loaded: true;
  visible: boolean;
  opacity: number;
  display: {
    brightness?: number;
    contrast?: number;
    surface_color_mode?: SurfaceColorMode;
  };
}

export interface GuiCommand {
  id: string;
  type: 'load_layer' | 'remove_layers' | 'reorder_layer' | 'set_layer_visibility' | 'set_layer_display' | 'move_cursor';
  payload: Record<string, unknown>;
  created_at: string;
  expires_at: string;
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
  features: Record<string, boolean>;
  workspaces: WorkspaceSummary[];
  default_workspace_id: string | null;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  description?: string | null;
  role: string;
  kind: string;
  is_default: boolean;
  case_count?: number;
}

export interface MonitoringStatusItem {
  name: string;
  status: 'ok' | 'degraded' | 'down' | 'unknown';
  message?: string | null;
  details: Record<string, unknown>;
}

interface MonitoringUserSummary {
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
}

interface PersistedIntensityVolumeLayer extends PersistedBaseLayer {
  type: 'intensity';
  brightness: number;
  contrast: number;
}

interface PersistedSegmentationVolumeLayer extends PersistedBaseLayer {
  type: 'segmentation';
  lut?: 'freesurfer' | 'binary';
  customLutUrl?: string;
  brightness: number;
  contrast: number;
}

interface PersistedSurfaceLayer extends PersistedBaseLayer {
  type: 'surface';
  surfaceColorMode?: SurfaceColorMode;
  curvatureUrl?: string;
  annotationUrl?: string;
  curvatureNegativeThreshold?: number;
  curvaturePositiveThreshold?: number;
}

/** Serialisable subset of Volume stored in localStorage. */
export type PersistedVolume = PersistedIntensityVolumeLayer | PersistedSegmentationVolumeLayer | PersistedSurfaceLayer;

/** Per-case persistence envelope stored in localStorage. */
export interface CaseState {
  version: 1;
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
  call_id?: string | null;
  execution_id?: string | null;
  ledger_status?: string | null;
  external_run_id?: string | null;
  name: string;
  arguments: Record<string, unknown>;
  result: string;
  is_error?: boolean;
  details?: Record<string, unknown>;
  artifacts?: Record<string, unknown>[];
  terminal?: boolean;
  elapsed_ms?: number | null;
}

export interface ReasoningEntry {
  summary: string;
  round?: number;
  tool_names?: string[];
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'info' | 'tool-calls';
  content: string | ChatContentPart[];
  severity?: 'warning';
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
  is_default: boolean;
  vision: boolean;
  configured: boolean;
  reachable: boolean;
  configuration_reason?: string | null;
  reachability_reason?: string | null;
}

export interface ArtifactListItem {
  id: string;
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
