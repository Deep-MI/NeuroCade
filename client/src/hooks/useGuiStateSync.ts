import { useEffect } from 'react'

import type { GuiStateSyncResponse, Volume } from '../types'
import * as api from '../utils/api'

interface UseGuiStateSyncOptions {
  workspaceId: string | null
  caseId: string | null
  guiSessionId: string
  runStatus: string
  volumes: Volume[]
  currentCaseId: string | null
  currentIntensityArtifactId: string | null
  currentIntensityVolume: string | null
  isRunActive: (status: string) => boolean
  onSyncResponse: (response: GuiStateSyncResponse) => void
  onError?: (error: unknown) => void
}

export function useGuiStateSync({
  workspaceId,
  caseId,
  guiSessionId,
  runStatus,
  volumes,
  currentCaseId,
  currentIntensityArtifactId,
  currentIntensityVolume,
  isRunActive,
  onSyncResponse,
  onError,
}: UseGuiStateSyncOptions): void {
  useEffect(() => {
    const syncState = () => {
      const loadedVolumeNames = volumes.map(v => v.filename)
      api.syncGuiState({
        workspace_id: workspaceId,
        case_id: caseId,
        gui_session_id: guiSessionId,
        is_job_running: isRunActive(runStatus),
        has_valid_segmentation: volumes.some(v => v.type === 'segmentation'),
        current_case_id: currentCaseId,
        loaded_volumes: loadedVolumeNames,
        loaded_volume_names: loadedVolumeNames,
        visible_volumes: volumes.filter(v => v.visible).map(v => v.filename),
        current_intensity_artifact_id: currentIntensityArtifactId,
        current_intensity_volume: currentIntensityVolume,
      }).then(onSyncResponse).catch(error => {
        onError?.(error)
      })
    }

    const interval = setInterval(syncState, 2000)
    syncState()

    return () => clearInterval(interval)
  }, [caseId, currentCaseId, currentIntensityArtifactId, currentIntensityVolume, guiSessionId, isRunActive, onError, onSyncResponse, runStatus, volumes, workspaceId])
}
