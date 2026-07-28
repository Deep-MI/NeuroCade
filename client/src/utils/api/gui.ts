import type { GuiLayerState, GuiStateSyncResponse } from '../../types';

import { appJson, jsonRequest } from './core';

export interface GuiStatePush {
  workspace_id: string | null;
  case_id: string | null;
  gui_session_id: string;
  is_job_running: boolean;
  current_case_id: string | null;
  layers: GuiLayerState[];
  acknowledged_command_ids: string[];
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
