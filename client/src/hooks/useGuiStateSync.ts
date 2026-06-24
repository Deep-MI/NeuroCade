import { useEffect, useMemo, useRef } from 'react'

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
  const payload = useMemo(() => {
    const loadedVolumeNames = volumes.map(v => v.filename)
    return {
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
    }
  }, [
    caseId,
    currentCaseId,
    currentIntensityArtifactId,
    currentIntensityVolume,
    guiSessionId,
    isRunActive,
    runStatus,
    volumes,
    workspaceId,
  ])

  const signature = useMemo(() => JSON.stringify(payload), [payload])
  const lastSyncedSignatureRef = useRef<string | null>(null)
  const payloadRef = useRef(payload)
  const onSyncResponseRef = useRef(onSyncResponse)
  const onErrorRef = useRef(onError)
  payloadRef.current = payload
  onSyncResponseRef.current = onSyncResponse
  onErrorRef.current = onError

  useEffect(() => {
    const syncState = () => {
      const currentSignature = JSON.stringify(payloadRef.current)
      if (lastSyncedSignatureRef.current === currentSignature) return
      lastSyncedSignatureRef.current = currentSignature
      api.syncGuiState(payloadRef.current).then(onSyncResponseRef.current).catch(error => {
        lastSyncedSignatureRef.current = null
        onErrorRef.current?.(error)
      })
    }

    const interval = setInterval(syncState, 2000)
    const timeout = setTimeout(syncState, 350)

    return () => {
      clearInterval(interval)
      clearTimeout(timeout)
    }
  }, [signature])
}
