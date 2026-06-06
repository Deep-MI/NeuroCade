import type { GuiStateSyncResponse } from '../../types';

import { appJson, jsonRequest } from './core';

export interface GuiStatePush {
  workspace_id: string | null;
  case_id: string | null;
  gui_session_id: string;
  is_job_running: boolean;
  has_valid_segmentation: boolean;
  current_case_id: string | null;
  loaded_volumes: string[];
  loaded_volume_names: string[];
  visible_volumes: string[];
  current_intensity_artifact_id: string | null;
  current_intensity_volume: string | null;
  current_cursor?: {
    voxel: [number, number, number];
    label_id: number;
    label_name: string;
  } | null;
}

export async function syncGuiState(state: GuiStatePush): Promise<GuiStateSyncResponse> {
  return appJson<GuiStateSyncResponse>('/gui/state', 'Failed to sync GUI state', jsonRequest(state, {
    method: 'POST',
  }));
}
