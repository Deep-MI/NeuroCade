import { NVImage } from '@niivue/niivue';

import type { Volume } from '../types.js';
import { appFetchUrl } from '../utils/api.js';
import type { NiivueVolumeInterop } from '../utils/niivueInterop.js';

// Pure, dependency-light drawing helpers live in nativeDrawingHelpers so they can
// be unit tested without pulling in the Niivue/runtime API surface.
export * from './nativeDrawingHelpers.js';

export async function loadDrawingSourceImage(source: Volume, signal?: AbortSignal): Promise<NiivueVolumeInterop> {
  const response = await appFetchUrl(source.url, { signal });
  if (!response.ok) {
    throw new Error(`Failed to load ${source.filename}: ${response.status}`);
  }
  const buffer = await response.arrayBuffer();
  const image = await NVImage.loadFromUrl({
    url: source.filename || source.name,
    name: source.filename || source.name,
    buffer,
  });
  return image as NiivueVolumeInterop;
}
