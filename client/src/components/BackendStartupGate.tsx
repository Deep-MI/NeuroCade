import { useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, ExternalLink, LoaderCircle, RotateCcw, TerminalSquare } from 'lucide-react'

import { APP_DISPLAY_NAME } from '../constants'
import { BASE } from '../utils/api/core'

const healthPollMs = 1500
const healthRequestTimeoutMs = 2500
const startupTimeoutMs = 600_000

type StartupStatus = 'checking' | 'ready' | 'timeout'

function healthEndpoint() {
  return `${BASE.replace(/\/+$/, '')}/healthz`
}

function isLocalHost() {
  const host = window.location.hostname
  return host === 'localhost' || host === '127.0.0.1' || host === '::1'
}

function isFrontendDevServer() {
  return import.meta.env.DEV && isLocalHost()
}

function localGatewayUrl() {
  return `${window.location.protocol}//${window.location.hostname === '127.0.0.1' ? '127.0.0.1' : 'localhost'}:8000`
}

function formatElapsed(ms: number) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes === 0) return `${seconds}s`
  return `${minutes}m ${seconds.toString().padStart(2, '0')}s`
}

async function checkHealth(url: string) {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), healthRequestTimeoutMs)
  try {
    const response = await fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
    })
    return response.ok
  } catch {
    return false
  } finally {
    window.clearTimeout(timeoutId)
  }
}

function BackendStartupScreen({
  attempts,
  elapsedMs,
  healthUrl,
  status,
  onRetry,
}: {
  attempts: number
  elapsedMs: number
  healthUrl: string
  status: StartupStatus
  onRetry: () => void
}) {
  const local = isLocalHost()
  const devFrontendOnly = isFrontendDevServer()
  const timedOut = status === 'timeout'
  const title = timedOut
    ? `${APP_DISPLAY_NAME} backend did not respond`
    : devFrontendOnly
      ? 'Frontend is ready. Backend is not connected yet'
      : local
        ? `Starting ${APP_DISPLAY_NAME} services`
        : `Connecting to ${APP_DISPLAY_NAME} services`
  const detail = timedOut
    ? 'The web UI loaded, but the API never became reachable. The most common cause is that the local backend container is not running.'
    : devFrontendOnly
      ? 'You are viewing the Vite frontend directly. Start the local NeuroCade backend, then open the app URL so the API and viewer are connected.'
      : local
        ? 'The local NeuroCade backend may still be starting. The workspace will open automatically when the API is healthy.'
        : 'The workspace will open automatically when the API is healthy.'
  const gatewayUrl = localGatewayUrl()
  const recoveryCommand = devFrontendOnly
    ? './scripts/run.sh start\n# then open http://localhost:8000'
    : './scripts/run.sh start'
  const checks = [
    {
      label: 'Web UI',
      state: 'Ready',
      detail: window.location.origin,
      icon: CheckCircle2,
      tone: 'nc-chip-green',
    },
    {
      label: 'Application API',
      state: timedOut ? 'No response' : 'Waiting',
      detail: healthUrl,
      icon: timedOut ? AlertTriangle : CircleDashed,
      tone: timedOut ? 'nc-chip-yellow' : 'nc-chip-blue',
    },
    {
      label: 'NeuroCade backend',
      state: devFrontendOnly || timedOut ? 'Needs a check' : 'Starting',
      detail: './scripts/desktop/run_backend.sh',
      icon: TerminalSquare,
      tone: '',
    },
  ]

  return (
    <main className="nc-app-page flex items-center justify-center px-6 py-10">
      <section className="nc-card-static w-full max-w-4xl p-8">
        <div className="flex items-start gap-4">
          <div className={`rounded border p-3 ${timedOut ? 'border-[var(--nc-warning-border)] bg-[var(--nc-warning-bg)] text-[var(--nc-warning)]' : 'border-[var(--nc-interactive-border)] bg-[var(--nc-interactive-subtle)] text-[var(--nc-interactive)]'}`}>
            <LoaderCircle className={`h-6 w-6 ${timedOut ? '' : 'animate-spin'}`} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="nc-eyebrow">{APP_DISPLAY_NAME} startup</p>
            <h1 className="mt-2 text-2xl font-semibold text-[var(--nc-tx)]">{title}</h1>
            <p className="mt-3 text-sm leading-6 text-[var(--nc-tx-muted)]">{detail}</p>
          </div>
        </div>

        <div className="mt-7 grid gap-3 lg:grid-cols-3">
          {checks.map((check) => {
            const Icon = check.icon
            return (
              <div key={check.label} className="nc-card-static px-4 py-4">
                <div className="flex items-center gap-3">
                  <span className={`nc-chip p-2 ${check.tone}`}>
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div>
                    <div className="nc-eyebrow">{check.label}</div>
                    <div className="mt-1 text-sm font-medium text-[var(--nc-tx)]">{check.state}</div>
                  </div>
                </div>
                <div className="nc-mono mt-3 break-all text-xs text-[var(--nc-tx-dim)]">{check.detail}</div>
              </div>
            )
          })}
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <div className="nc-card-static px-4 py-3">
            <div className="nc-eyebrow">Elapsed</div>
            <div className="nc-mono mt-1 text-sm text-[var(--nc-tx)]">{formatElapsed(elapsedMs)}</div>
          </div>
          <div className="nc-card-static px-4 py-3">
            <div className="nc-eyebrow">Checks</div>
            <div className="nc-mono mt-1 text-sm text-[var(--nc-tx)]">{attempts}</div>
          </div>
          <div className="nc-card-static px-4 py-3">
            <div className="nc-eyebrow">Timeout</div>
            <div className="nc-mono mt-1 text-sm text-[var(--nc-tx)]">{formatElapsed(startupTimeoutMs)}</div>
          </div>
        </div>

        <div className="mt-5 rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] px-4 py-4 text-[var(--nc-tx)]">
          <div className="nc-eyebrow mb-2 flex items-center gap-2">
            <TerminalSquare className="h-4 w-4" aria-hidden="true" />
            Try this from the repo root
          </div>
          <pre className="nc-mono overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{recoveryCommand}</pre>
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          {local && (
            <a
              href={gatewayUrl}
              className="nc-btn nc-btn-active"
            >
              <ExternalLink className="h-4 w-4" aria-hidden="true" />
              Open local app
            </a>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="nc-btn"
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            Retry connection
          </button>
        </div>
      </section>
    </main>
  )
}

export function BackendStartupGate({ children }: PropsWithChildren) {
  const [status, setStatus] = useState<StartupStatus>('checking')
  const [attempts, setAttempts] = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [retryKey, setRetryKey] = useState(0)
  const healthUrl = useMemo(() => healthEndpoint(), [])

  useEffect(() => {
    let cancelled = false
    const startedAt = Date.now()

    const tickId = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt)
    }, 1000)

    const poll = async () => {
      while (!cancelled) {
        const elapsed = Date.now() - startedAt
        setElapsedMs(elapsed)
        if (elapsed >= startupTimeoutMs) {
          setStatus('timeout')
          return
        }

        setAttempts((current) => current + 1)
        if (await checkHealth(healthUrl)) {
          if (!cancelled) setStatus('ready')
          return
        }

        await new Promise((resolve) => window.setTimeout(resolve, healthPollMs))
      }
    }

    void poll()

    return () => {
      cancelled = true
      window.clearInterval(tickId)
    }
  }, [healthUrl, retryKey])

  if (status === 'ready') {
    return <>{children}</>
  }

  return (
    <BackendStartupScreen
      attempts={attempts}
      elapsedMs={elapsedMs}
      healthUrl={healthUrl}
      status={status}
      onRetry={() => {
        setStatus('checking')
        setAttempts(0)
        setElapsedMs(0)
        setRetryKey((current) => current + 1)
      }}
    />
  )
}
