import { useEffect, useMemo, useRef } from 'react'

import type { GuiStateSyncResponse, Volume } from '../types'
import * as api from '../utils/api'

const GUI_STATE_SYNC_INTERVAL_MS = 60000

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
    const loadedVolumeNames: string[] = []
    const visibleVolumeNames: string[] = []
    let hasValidSegmentation = false

    for (const volume of volumes) {
      loadedVolumeNames.push(volume.filename)
      if (volume.visible) visibleVolumeNames.push(volume.filename)
      if (volume.type === 'segmentation') hasValidSegmentation = true
    }

    return { loadedVolumeNames, visibleVolumeNames, hasValidSegmentation }
  }, [volumes])

  const payload = useMemo(() => {
    return {
      workspace_id: workspaceId,
      case_id: caseId,
      gui_session_id: guiSessionId,
      is_job_running: isRunActive(runStatus),
      has_valid_segmentation: volumeSnapshot.hasValidSegmentation,
      current_case_id: currentCaseId,
      loaded_volumes: volumeSnapshot.loadedVolumeNames,
      loaded_volume_names: volumeSnapshot.loadedVolumeNames,
      visible_volumes: volumeSnapshot.visibleVolumeNames,
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

  const signature = useMemo(() => [
    workspaceId ?? '',
    caseId ?? '',
    guiSessionId,
    isRunActive(runStatus) ? '1' : '0',
    volumeSnapshot.hasValidSegmentation ? '1' : '0',
    currentCaseId ?? '',
    volumeSnapshot.loadedVolumeNames.map((name) => `${name.length}:${name}`).join('|'),
    volumeSnapshot.visibleVolumeNames.map((name) => `${name.length}:${name}`).join('|'),
    currentIntensityArtifactId ?? '',
    currentIntensityVolume ?? '',
  ].join('\u001f'), [
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
  const lastSyncedSignatureRef = useRef<string | null>(null)
  const signatureRef = useRef(signature)
  const payloadRef = useRef(payload)
  const onSyncResponseRef = useRef(onSyncResponse)
  const onErrorRef = useRef(onError)
  signatureRef.current = signature
  payloadRef.current = payload
  onSyncResponseRef.current = onSyncResponse
  onErrorRef.current = onError

  useEffect(() => {
    const syncState = () => {
      const currentSignature = signatureRef.current
      if (lastSyncedSignatureRef.current === currentSignature) return
      lastSyncedSignatureRef.current = currentSignature
      api.syncGuiState(payloadRef.current).then(onSyncResponseRef.current).catch(error => {
        lastSyncedSignatureRef.current = null
        onErrorRef.current?.(error)
      })
    }

    const interval = window.setInterval(syncState, GUI_STATE_SYNC_INTERVAL_MS)

    return () => {
      window.clearInterval(interval)
    }
  }, [])
}
