import { useEffect, useMemo, useRef } from 'react'

import type { GuiStateSyncResponse, Volume } from '../types'
import * as api from '../utils/api'
import { startGuiStateSync } from '../utils/guiStateSync'
import { useRequestScope } from './useRequestScope'

interface UseGuiStateSyncOptions {
  workspaceId: string | null
  caseId: string | null
  guiSessionId: string
  runStatus: string
  volumes: Volume[]
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
  currentIntensityArtifactId,
  currentIntensityVolume,
  isRunActive,
  onSyncResponse,
  onError,
}: UseGuiStateSyncOptions): void {
  const requestScope = useRequestScope(JSON.stringify([workspaceId, caseId, guiSessionId]))
  const volumeSnapshot = useMemo(() => {
    return volumes.map(volume => {
      const surfaceMatch = /^([lr]h)\.([^.]+)$/.exec(volume.filename.split('/').pop() ?? volume.filename)
      return {
        id: volume.id,
        artifact_id: volume.artifactId,
        filename: volume.filename,
        name: volume.name,
        type: volume.type,
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
      layers: volumeSnapshot,
      current_intensity_artifact_id: currentIntensityArtifactId,
      current_intensity_volume: currentIntensityVolume,
    }
  }, [
    caseId,
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
  payloadRef.current = payload
  onSyncResponseRef.current = onSyncResponse
  onErrorRef.current = onError

  useEffect(() => {
    const isCurrent = requestScope.capture()
    return startGuiStateSync({
      payload: () => payloadRef.current,
      sync: api.syncGuiState,
      onResponse: response => { if (isCurrent()) onSyncResponseRef.current(response) },
      onError: error => { if (isCurrent()) onErrorRef.current?.(error) },
    })
  }, [caseId, guiSessionId, requestScope, workspaceId])
}
