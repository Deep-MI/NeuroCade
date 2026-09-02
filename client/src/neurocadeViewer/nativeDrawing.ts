import type { Volume } from '../types.js';
import { appFetchUrl } from '../utils/api.js';

// Pure, dependency-light drawing helpers live in nativeDrawingHelpers so they can
// be unit tested without pulling in the Niivue/runtime API surface.
export * from './nativeDrawingHelpers.js';

export async function loadDrawingSourceFile(source: Volume, signal?: AbortSignal): Promise<File> {
  const response = await appFetchUrl(source.url, { signal });
  if (!response.ok) {
    throw new Error(`Failed to load ${source.filename}: ${response.status}`);
  }
  return new File([await response.arrayBuffer()], source.filename || source.name);
}
