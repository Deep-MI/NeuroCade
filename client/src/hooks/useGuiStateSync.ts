import { useEffect, useMemo, useRef } from 'react'

import type { GuiStateSyncResponse, Volume } from '../types'
import * as api from '../utils/api'

const GUI_STATE_SYNC_INTERVAL_MS = 2000

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
  const volumeSnapshot = useMemo(() => {
    return volumes.map(volume => {
      const surfaceMatch = /^([lr]h)\.([^.]+)$/.exec(volume.filename.split('/').pop() ?? volume.filename)
      return {
        id: volume.id,
        artifact_id: volume.artifactId,
        filename: volume.filename,
        name: volume.name,
        type: volume.type ?? 'intensity',
        role: surfaceMatch?.[2] ?? (volume.type === 'segmentation' ? 'segmentation' : 'intensity'),
        hemisphere: surfaceMatch?.[1] === 'lh' ? 'left' as const : surfaceMatch?.[1] === 'rh' ? 'right' as const : undefined,
        loaded: true as const,
        visible: volume.visible,
        opacity: volume.opacity,
        display: {
          brightness: 'brightness' in volume ? volume.brightness : undefined,
          contrast: 'contrast' in volume ? volume.contrast : undefined,
          surface_color_mode: volume.type === 'surface' ? volume.surfaceColorMode : undefined,
        },
      }
    })
  }, [volumes])

  const payload = useMemo(() => {
    return {
      workspace_id: workspaceId,
      case_id: caseId,
      gui_session_id: guiSessionId,
      is_job_running: isRunActive(runStatus),
      current_case_id: currentCaseId,
      layers: volumeSnapshot,
      current_intensity_artifact_id: currentIntensityArtifactId,
      current_intensity_volume: currentIntensityVolume,
    }
  }, [
    caseId,
    currentCaseId,
    currentIntensityArtifactId,
    currentIntensityVolume,
    guiSessionId,
    isRunActive,
    runStatus,
    volumeSnapshot,
    workspaceId,
  ])

  const payloadRef = useRef(payload)
  const onSyncResponseRef = useRef(onSyncResponse)
  const onErrorRef = useRef(onError)
  const acknowledgedCommandIdsRef = useRef<string[]>([])
  payloadRef.current = payload
  onSyncResponseRef.current = onSyncResponse
  onErrorRef.current = onError

  useEffect(() => {
    let disposed = false
    let syncInFlight = false

    const syncState = () => {
      if (syncInFlight) return
      syncInFlight = true
      api.syncGuiState({
        ...payloadRef.current,
        acknowledged_command_ids: acknowledgedCommandIdsRef.current,
      })
        .then(response => {
          if (!disposed) {
            onSyncResponseRef.current(response)
            acknowledgedCommandIdsRef.current = response.commands.map(command => command.id)
          }
        })
        .catch(error => {
          if (!disposed) onErrorRef.current?.(error)
        })
        .finally(() => {
          syncInFlight = false
        })
    }

    syncState()
    const interval = window.setInterval(syncState, GUI_STATE_SYNC_INTERVAL_MS)

    return () => {
      disposed = true
      window.clearInterval(interval)
    }
  }, [])
}
