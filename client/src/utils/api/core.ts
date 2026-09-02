import type { ErrorResponse } from '../../types';

export const BASE = import.meta.env.VITE_API_URL ?? '/api/app';

type AccessTokenProvider = () => Promise<string | null>;

let accessTokenProvider: AccessTokenProvider = () => Promise.resolve(null);

export function setAccessTokenProvider(provider: AccessTokenProvider) {
  accessTokenProvider = provider;
}

async function buildHeaders(init?: HeadersInit): Promise<Headers> {
  const headers = new Headers(init);
  const token = await accessTokenProvider();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return headers;
}

function shouldAttachAppAuth(url: string): boolean {
  if (url.startsWith('blob:') || url.startsWith('data:')) {
    return false;
  }

  const origin = globalThis.location?.origin ?? 'http://localhost';
  const target = new URL(url, origin);
  const apiBase = new URL(BASE, origin);
  const apiPath = apiBase.pathname.endsWith('/') ? apiBase.pathname : `${apiBase.pathname}/`;
  return target.origin === apiBase.origin && (
    target.pathname === apiBase.pathname || target.pathname.startsWith(apiPath)
  );
}

export async function appFetchUrl(url: string, init: RequestInit = {}): Promise<Response> {
  if (url.startsWith('blob:') || url.startsWith('data:')) {
    return fetch(url, init);
  }
  return fetch(url, {
    ...init,
    headers: shouldAttachAppAuth(url) ? await buildHeaders(init.headers) : new Headers(init.headers),
  });
}

export function appUrl(pathOrUrl: string): string {
  if (pathOrUrl.startsWith('blob:') || pathOrUrl.startsWith('data:') || /^https?:\/\//i.test(pathOrUrl)) {
    return pathOrUrl;
  }
  if (pathOrUrl.startsWith(BASE)) {
    return pathOrUrl;
  }
  const path = pathOrUrl.startsWith('/') ? pathOrUrl : `/${pathOrUrl}`;
  return `${BASE}${path}`;
}

export async function appFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return appFetchUrl(`${BASE}${path}`, init);
}

export async function parseError(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as ErrorResponse;
    return body.detail ?? body.error ?? body.message ?? fallback;
  } catch {
    const text = await res.text().catch(() => '');
    return text || fallback;
  }
}

export async function expectOk(res: Response, fallback: string): Promise<void> {
  if (!res.ok) {
    throw new Error(await parseError(res, fallback));
  }
}

async function responseJson<T>(res: Response, fallback: string): Promise<T> {
  await expectOk(res, fallback);
  return await res.json() as T;
}

export async function appJson<T>(path: string, fallback: string, init: RequestInit = {}): Promise<T> {
  return responseJson<T>(await appFetch(path, init), fallback);
}

export async function appOk(path: string, fallback: string, init: RequestInit = {}): Promise<void> {
  await expectOk(await appFetch(path, init), fallback);
}

export function jsonRequest(body: unknown, init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers);
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return {
    ...init,
    headers,
    body: JSON.stringify(body),
  };
}
