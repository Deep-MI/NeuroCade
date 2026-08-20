import { useEffect, useRef } from 'react'

import type { StatusResponse } from '../types'
import { terminalRunTransitionKey } from '../utils/runNotifications'

interface UseCasePollingOptions {
  activeCaseId: string | null
  runId: string | null
  runStatus: string
  isRunActive: (status: string) => boolean
  fetchStatus: (caseId: string) => Promise<StatusResponse>
  fetchLogs: (caseId: string) => Promise<void>
  fetchOutputs: (caseId: string) => Promise<void>
  onRunChange: (status: string, runId?: string) => void
  onTerminalStatus?: (status: string, runId: string, workflowId?: string) => void
  onError?: (error: unknown) => void
}

export function useCasePolling({
  activeCaseId,
  runId,
  runStatus,
  isRunActive,
  fetchStatus,
  fetchLogs,
  fetchOutputs,
  onRunChange,
  onTerminalStatus,
  onError,
}: UseCasePollingOptions): void {
  const emittedTerminalTransitionsRef = useRef(new Set<string>())

  useEffect(() => {
    emittedTerminalTransitionsRef.current.clear()
  }, [activeCaseId])

  useEffect(() => {
    let statusInterval: ReturnType<typeof setInterval> | undefined
    let logInterval: ReturnType<typeof setInterval> | undefined
    let outputInterval: ReturnType<typeof setInterval> | undefined

    if (activeCaseId) {
      statusInterval = setInterval(async () => {
        try {
          const data = await fetchStatus(activeCaseId)
          if (!data.status || data.status === 'unknown') return
          if (data.status === runStatus && data.runId === runId) return

          const transitionKey = terminalRunTransitionKey({ runId: runId ?? undefined, status: runStatus }, data)
          onRunChange(data.status, data.runId)
          if (transitionKey && data.runId && !emittedTerminalTransitionsRef.current.has(transitionKey)) {
            emittedTerminalTransitionsRef.current.add(transitionKey)
            await fetchLogs(activeCaseId)
            await fetchOutputs(activeCaseId)
            onTerminalStatus?.(data.status, data.runId, data.workflowId)
          }
        } catch (error) {
          onError?.(error)
        }
      }, isRunActive(runStatus) ? 2000 : 3000)

    }
    if (activeCaseId && isRunActive(runStatus)) {
      logInterval = setInterval(() => {
        void fetchLogs(activeCaseId).catch(onError)
      }, 3000)

      outputInterval = setInterval(() => {
        void fetchOutputs(activeCaseId).catch(onError)
      }, 10000)
    }

    return () => {
      if (statusInterval) clearInterval(statusInterval)
      if (logInterval) clearInterval(logInterval)
      if (outputInterval) clearInterval(outputInterval)
    }
  }, [
    activeCaseId,
    runId,
    fetchLogs,
    fetchOutputs,
    fetchStatus,
    isRunActive,
    runStatus,
    onError,
    onRunChange,
    onTerminalStatus,
  ])
}
