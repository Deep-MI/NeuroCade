import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'
import './index.css'
import { AppSessionProvider, RequireAuth } from './auth/AppSession.tsx'
import { FrontendConfigProvider } from './auth/FrontendConfigProvider.tsx'
import { BackendStartupGate } from './components/BackendStartupGate.tsx'
import { RouteErrorPage } from './components/RouteErrorPage.tsx'
import { installGlobalErrorReporting } from './utils/monitoring.ts'

const DefaultWorkspaceRedirectPage = lazy(() => import('./pages/DefaultWorkspaceRedirectPage.tsx').then(module => ({ default: module.DefaultWorkspaceRedirectPage })))
const CasesLayout = lazy(() => import('./pages/CasesLayout.tsx').then(module => ({ default: module.CasesLayout })))
const CaseListPage = lazy(() => import('./pages/CaseListPage.tsx').then(module => ({ default: module.CaseListPage })))
const CaseDetailPage = lazy(() => import('./pages/CaseDetailPage.tsx').then(module => ({ default: module.CaseDetailPage })))
const SignInPage = lazy(() => import('./pages/SignInPage.tsx').then(module => ({ default: module.SignInPage })))
const SignUpPage = lazy(() => import('./pages/SignUpPage.tsx').then(module => ({ default: module.SignUpPage })))
const MonitoringPage = lazy(() => import('./pages/MonitoringPage.tsx').then(module => ({ default: module.MonitoringPage })))

installGlobalErrorReporting()

const router = createBrowserRouter([
  {
    errorElement: <RouteErrorPage />,
    children: [
      {
        path: '/',
        element: (
          <RequireAuth>
            <DefaultWorkspaceRedirectPage />
          </RequireAuth>
        ),
      },
      {
        path: '/sign-in/*',
        element: <SignInPage />,
      },
      {
        path: '/sign-up/*',
        element: <SignUpPage />,
      },
      {
        path: '/monitoring',
        element: (
          <RequireAuth>
            <MonitoringPage />
          </RequireAuth>
        ),
      },
      {
        path: '/workspaces/:workspaceId/cases',
        element: (
          <RequireAuth>
            <CasesLayout />
          </RequireAuth>
        ),
        children: [
          {
            index: true,
            element: <CaseListPage />,
          },
          {
            path: ':caseId',
            element: <CaseDetailPage />,
          },
        ],
      },
      {
        path: '*',
        element: <Navigate to="/" replace />,
      },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BackendStartupGate>
      <FrontendConfigProvider>
        <AppSessionProvider>
          <Suspense fallback={<div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-tx-muted)]">Loading workspace...</div>}>
            <RouterProvider router={router} />
          </Suspense>
        </AppSessionProvider>
      </FrontendConfigProvider>
    </BackendStartupGate>
  </StrictMode>,
)
