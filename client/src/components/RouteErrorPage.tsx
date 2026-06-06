import { isRouteErrorResponse, Link, useLocation, useRouteError } from 'react-router-dom';
import { useEffect } from 'react';

import { reportFrontendError } from '../utils/monitoring';


export function RouteErrorPage() {
  const error = useRouteError();
  const location = useLocation();

  let title = 'Something went wrong';
  let detail = 'The application hit an unexpected routing error.';

  if (isRouteErrorResponse(error)) {
    if (error.status === 404) {
      title = 'Page Not Found';
      detail = 'This route is no longer available. The app may have been reset or the URL may be stale.';
    } else {
      title = `${error.status} ${error.statusText || 'Request failed'}`;
      detail = typeof error.data === 'string' && error.data ? error.data : detail;
    }
  } else if (error instanceof Error && error.message) {
    detail = error.message;
  }

  useEffect(() => {
    reportFrontendError('frontend.route_error', error, {
      path: location.pathname,
      route_status: isRouteErrorResponse(error) ? error.status : undefined,
    });
  }, [error, location.pathname]);

  return (
    <main className="nc-app-page px-6 py-12">
      <div className="nc-card-static mx-auto max-w-2xl p-8">
        <p className="nc-eyebrow">Routing Error</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-[var(--nc-tx)]">{title}</h1>
        <p className="mt-4 text-sm leading-6 text-[var(--nc-tx-muted)]">{detail}</p>
        <p className="nc-mono mt-2 text-xs text-[var(--nc-tx-faint)]">Current path: {location.pathname}</p>
        <div className="mt-6">
          <Link
            to="/"
            replace
            className="nc-btn nc-btn-active"
          >
            Return Home
          </Link>
        </div>
      </div>
    </main>
  );
}
