import { useEffect } from 'react'

interface UseCasePollingOptions {
  activeCaseId: string | null
  runStatus: string
  isRunActive: (status: string) => boolean
  isRunTerminal: (status: string) => boolean
  fetchStatus: (caseId: string) => Promise<{ status?: string }>
  fetchLogs: (caseId: string) => Promise<void>
  fetchOutputs: (caseId: string) => Promise<void>
  onStatusChange: (status: string) => void
  onTerminalStatus?: (status: string) => void
  onError?: (error: unknown) => void
}

export function useCasePolling({
  activeCaseId,
  runStatus,
  isRunActive,
  isRunTerminal,
  fetchStatus,
  fetchLogs,
  fetchOutputs,
  onStatusChange,
  onTerminalStatus,
  onError,
}: UseCasePollingOptions): void {
  useEffect(() => {
    let statusInterval: ReturnType<typeof setInterval> | undefined
    let logInterval: ReturnType<typeof setInterval> | undefined
    let outputInterval: ReturnType<typeof setInterval> | undefined

    if (activeCaseId) {
      statusInterval = setInterval(async () => {
        try {
          const data = await fetchStatus(activeCaseId)
          if (!data.status || data.status === 'unknown' || data.status === runStatus) return
          onStatusChange(data.status)
          if (isRunTerminal(data.status)) {
            await fetchLogs(activeCaseId)
            await fetchOutputs(activeCaseId)
            onTerminalStatus?.(data.status)
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
    fetchLogs,
    fetchOutputs,
    fetchStatus,
    isRunActive,
    isRunTerminal,
    runStatus,
    onError,
    onStatusChange,
    onTerminalStatus,
  ])
}
