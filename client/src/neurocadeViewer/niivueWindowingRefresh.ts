import { asNiivueInterop, type NiivueInterop, type NiivueShaderInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import type { Niivue } from '@niivue/niivue';

const TEXTURE1_COLORMAPS = 33985;
const TEXTURE9_ORIENT = 33993;
const TEXTURE10_BLEND = 33994;

const NIFTI_INTENT_LABEL = 1002;
const DT_UINT8 = 2;
const DT_INT16 = 4;
const DT_FLOAT32 = 16;
const DT_FLOAT64 = 64;
const DT_UINT16 = 512;

const WINDOWING_PERF_FLAG = 'neurocade.windowingPerf';
const SLOW_WINDOWING_MS = 16;
const SLOW_TEXTURE_UPLOAD_MS = 24;

interface RawTextureCacheEntry {
  texture: WebGLTexture;
  sourceImage: ArrayLike<number> | undefined;
  frame4D: number;
  dimsKey: string;
  datatypeCode: number;
  orientShader: NiivueShaderInterop;
}

const rawTextureCache = new WeakMap<NiivueInterop, WeakMap<NiivueVolumeInterop, RawTextureCacheEntry>>();
const originalRefreshLayers = new WeakMap<NiivueInterop, NonNullable<NiivueInterop['refreshLayers']>>();
const patchedWindowingInstances = new WeakSet<NiivueInterop>();

export function shouldLogNiivueWindowingPerf(): boolean {
  try {
    return window.localStorage.getItem(WINDOWING_PERF_FLAG) === '1' || new URLSearchParams(window.location.search).get('windowingPerf') === '1';
  } catch {
    return false;
  }
}

export function logNiivueWindowingPerf(kind: string, durationMs: number, details: Record<string, unknown> = {}, slowThresholdMs = SLOW_WINDOWING_MS): void {
  if (!shouldLogNiivueWindowingPerf() && durationMs < slowThresholdMs) return;
  console.info('[NeuroCade windowing]', kind, `${durationMs.toFixed(1)} ms`, details);
}

function flattenMat4(value: number[] | number[][] | undefined): number[] | null {
  if (!value) return null;
  if (Array.isArray(value[0])) {
    const rows = value as number[][];
    if (rows.length < 4 || rows.some((row) => row.length < 4)) return null;
    return [
      rows[0][0], rows[0][1], rows[0][2], rows[0][3],
      rows[1][0], rows[1][1], rows[1][2], rows[1][3],
      rows[2][0], rows[2][1], rows[2][2], rows[2][3],
      rows[3][0], rows[3][1], rows[3][2], rows[3][3],
    ];
  }
  const flat = value as number[];
  return flat.length >= 16 ? Array.from(flat).slice(0, 16) : null;
}

function invertMat4(a: number[]): Float32Array | null {
  const out = new Float32Array(16);
  const a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3];
  const a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7];
  const a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11];
  const a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];

  const b00 = a00 * a11 - a01 * a10;
  const b01 = a00 * a12 - a02 * a10;
  const b02 = a00 * a13 - a03 * a10;
  const b03 = a01 * a12 - a02 * a11;
  const b04 = a01 * a13 - a03 * a11;
  const b05 = a02 * a13 - a03 * a12;
  const b06 = a20 * a31 - a21 * a30;
  const b07 = a20 * a32 - a22 * a30;
  const b08 = a20 * a33 - a23 * a30;
  const b09 = a21 * a32 - a22 * a31;
  const b10 = a21 * a33 - a23 * a31;
  const b11 = a22 * a33 - a23 * a32;

  let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  if (!det) return null;
  det = 1.0 / det;

  out[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det;
  out[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det;
  out[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det;
  out[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det;
  out[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det;
  out[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det;
  out[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det;
  out[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det;
  out[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det;
  out[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det;
  out[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det;
  out[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det;
  out[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det;
  out[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det;
  out[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det;
  out[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det;
  return out;
}

function prepareLayerData(volume: NiivueVolumeInterop): ArrayLike<number> | undefined {
  const img = volume.img;
  const frame4D = volume.frame4D ?? 0;
  if (!img || frame4D <= 0 || !volume.nFrame4D || frame4D >= volume.nFrame4D || !volume.nVox3D) return img;
  const sliceable = img as ArrayLike<number> & { slice?: (start?: number, end?: number) => ArrayLike<number> };
  if (typeof sliceable.slice !== 'function') return img;
  return sliceable.slice(frame4D * volume.nVox3D, (frame4D + 1) * volume.nVox3D);
}

function selectOrientShader(nv: NiivueInterop, datatypeCode: number): NiivueShaderInterop | null {
  if (datatypeCode === DT_INT16) return nv.orientShaderI ?? null;
  if (datatypeCode === DT_FLOAT32 || datatypeCode === DT_FLOAT64) return nv.orientShaderF ?? null;
  if (datatypeCode === DT_UINT8 || datatypeCode === DT_UINT16) return nv.orientShaderU ?? null;
  return null;
}

function ensureCanvasCache(nv: NiivueInterop): WeakMap<NiivueVolumeInterop, RawTextureCacheEntry> {
  let cache = rawTextureCache.get(nv);
  if (!cache) {
    cache = new WeakMap();
    rawTextureCache.set(nv, cache);
  }
  return cache;
}

function createRawTexture(
  gl: WebGL2RenderingContext,
  volume: NiivueVolumeInterop,
  img: ArrayLike<number>,
): WebGLTexture | null {
  const hdr = volume.hdr;
  const dims = hdr?.dims;
  if (!hdr?.datatypeCode || !dims) return null;

  const texture = gl.createTexture();
  if (!texture) return null;
  gl.activeTexture(TEXTURE9_ORIENT);
  gl.bindTexture(gl.TEXTURE_3D, texture);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);

  if (hdr.datatypeCode === DT_UINT8) {
    gl.texStorage3D(gl.TEXTURE_3D, 1, gl.R8UI, dims[1], dims[2], dims[3]);
    gl.texSubImage3D(gl.TEXTURE_3D, 0, 0, 0, 0, dims[1], dims[2], dims[3], gl.RED_INTEGER, gl.UNSIGNED_BYTE, img as Uint8Array);
  } else if (hdr.datatypeCode === DT_INT16) {
    gl.texStorage3D(gl.TEXTURE_3D, 1, gl.R16I, dims[1], dims[2], dims[3]);
    gl.texSubImage3D(gl.TEXTURE_3D, 0, 0, 0, 0, dims[1], dims[2], dims[3], gl.RED_INTEGER, gl.SHORT, img as Int16Array);
  } else if (hdr.datatypeCode === DT_FLOAT32) {
    gl.texStorage3D(gl.TEXTURE_3D, 1, gl.R32F, dims[1], dims[2], dims[3]);
    gl.texSubImage3D(gl.TEXTURE_3D, 0, 0, 0, 0, dims[1], dims[2], dims[3], gl.RED, gl.FLOAT, img as Float32Array);
  } else if (hdr.datatypeCode === DT_FLOAT64) {
    const img32 = Float32Array.from(img);
    gl.texStorage3D(gl.TEXTURE_3D, 1, gl.R32F, dims[1], dims[2], dims[3]);
    gl.texSubImage3D(gl.TEXTURE_3D, 0, 0, 0, 0, dims[1], dims[2], dims[3], gl.RED, gl.FLOAT, img32);
  } else if (hdr.datatypeCode === DT_UINT16) {
    gl.texStorage3D(gl.TEXTURE_3D, 1, gl.R16UI, dims[1], dims[2], dims[3]);
    gl.texSubImage3D(gl.TEXTURE_3D, 0, 0, 0, 0, dims[1], dims[2], dims[3], gl.RED_INTEGER, gl.UNSIGNED_SHORT, img as Uint16Array);
  } else {
    gl.deleteTexture(texture);
    return null;
  }

  return texture;
}

function ensureRawTexture(nv: NiivueInterop, volume: NiivueVolumeInterop): RawTextureCacheEntry | null {
  const gl = nv.gl;
  const hdr = volume.hdr;
  const dims = hdr?.dims;
  const datatypeCode = hdr?.datatypeCode;
  if (!gl || !dims || !datatypeCode) return null;
  if (hdr.intent_code === NIFTI_INTENT_LABEL || volume.colormapLabel) return null;
  if (volume.modulationImage !== undefined && volume.modulationImage !== null && volume.modulationImage >= 0) return null;
  if ((volume.colormapType ?? 0) !== 0 || (volume.colormapNegative ?? '').length > 0) return null;

  const orientShader = selectOrientShader(nv, datatypeCode);
  if (!orientShader) return null;

  const sourceImage = volume.img;
  const frame4D = volume.frame4D ?? 0;
  const dimsKey = `${dims[1]}:${dims[2]}:${dims[3]}`;
  const cache = ensureCanvasCache(nv);
  const cached = cache.get(volume);
  if (
    cached &&
    cached.sourceImage === sourceImage &&
    cached.frame4D === frame4D &&
    cached.dimsKey === dimsKey &&
    cached.datatypeCode === datatypeCode &&
    cached.orientShader === orientShader &&
    gl.isTexture(cached.texture)
  ) {
    return cached;
  }

  if (cached) gl.deleteTexture(cached.texture);
  const img = prepareLayerData(volume);
  if (!img) return null;
  const uploadStart = performance.now();
  const texture = createRawTexture(gl, volume, img);
  if (!texture) return null;
  logNiivueWindowingPerf('cache upload', performance.now() - uploadStart, {
    id: volume.id,
    name: volume.name,
    dims: dimsKey,
    datatypeCode,
    frame4D,
  }, SLOW_TEXTURE_UPLOAD_MS);

  const entry = { texture, sourceImage, frame4D, dimsKey, datatypeCode, orientShader };
  cache.set(volume, entry);
  return entry;
}

function configureColormapUniforms(gl: WebGL2RenderingContext, shader: NiivueShaderInterop, volume: NiivueVolumeInterop, layer: number, isAdditiveBlend: boolean): void {
  const colormapType = volume.colormapType ?? 0;
  const isColorbarFromZero = colormapType !== 0 ? 1 : 0;
  const isAlphaThreshold = colormapType === 1 ? 1 : 0;
  const colormapNegative = volume.colormapNegative ?? '';
  let minNegative = Number.POSITIVE_INFINITY;
  let maxNegative = Number.NEGATIVE_INFINITY;
  const calMin = volume.cal_min ?? 0;
  const calMax = volume.cal_max ?? 1;

  if (colormapNegative.length > 0) {
    minNegative = Math.min(-calMin, -calMax);
    maxNegative = Math.max(-calMin, -calMax);
  }

  gl.uniform1i(shader.uniforms.isAlphaThreshold ?? null, isAlphaThreshold);
  gl.uniform1i(shader.uniforms.isColorbarFromZero ?? null, isColorbarFromZero);
  gl.uniform1i(shader.uniforms.isAdditiveBlend ?? null, isAdditiveBlend ? 1 : 0);
  gl.uniform1f(shader.uniforms.layer ?? null, layer);
  gl.uniform1f(shader.uniforms.cal_minNeg ?? null, minNegative);
  gl.uniform1f(shader.uniforms.cal_maxNeg ?? null, maxNegative);
}

function refreshLayerOfVolume(nv: NiivueInterop, volume: NiivueVolumeInterop): number | null {
  let layer = 0;
  for (const candidate of nv.volumes) {
    if (candidate === volume) return volume.toRAS ? layer : null;
    if (candidate.toRAS) layer += 1;
  }
  return null;
}

export function refreshNiivueWindowingOnly(nvSource: Niivue, volume: NiivueVolumeInterop, layer: number): boolean {
  if (layer !== 0) return false;

  const start = performance.now();
  const nv = asNiivueInterop(nvSource);
  const gl = nv.gl;
  const backDims = nv.back?.dims;
  const hdr = volume.hdr;
  const toRAS = flattenMat4(volume.toRAS);
  const mtx = toRAS ? invertMat4(toRAS) : null;
  if (!gl || !hdr || !backDims || !mtx || !nv.volumeTexture || !nv.colormapTexture || !nv.genericVAO || !nv.rgbaTex) return false;
  if (!Number.isFinite(volume.cal_min) || !Number.isFinite(volume.cal_max) || volume.cal_min === volume.cal_max) return false;

  const rawTexture = ensureRawTexture(nv, volume);
  if (!rawTexture) return false;

  const framebuffer = gl.createFramebuffer();
  if (!framebuffer) return false;
  const blendTexture = nv.rgbaTex(null, TEXTURE10_BLEND, [2, 2, 2, 2]);

  const isWindowInverted = volume.cal_max! < volume.cal_min!;
  const originalColormapInvert = volume.colormapInvert ?? false;
  try {
    volume.colormapInvert = isWindowInverted ? !originalColormapInvert : originalColormapInvert;
    nv.refreshColormaps?.();
  } finally {
    volume.colormapInvert = originalColormapInvert;
  }
  gl.bindVertexArray(nv.genericVAO);
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
  gl.disable(gl.CULL_FACE);
  gl.disable(gl.BLEND);
  gl.viewport(0, 0, backDims[1], backDims[2]);

  rawTexture.orientShader.use(gl);
  gl.activeTexture(TEXTURE9_ORIENT);
  gl.bindTexture(gl.TEXTURE_3D, rawTexture.texture);
  gl.activeTexture(TEXTURE1_COLORMAPS);
  gl.bindTexture(gl.TEXTURE_2D, nv.colormapTexture);

  const shader = rawTexture.orientShader;
  gl.uniform1i(shader.uniforms.intensityVol ?? null, 9);
  gl.uniform1i(shader.uniforms.blend3D ?? null, 10);
  gl.uniform1i(shader.uniforms.colormap ?? null, 1);
  gl.uniform1i(shader.uniforms.modulationVol ?? null, 7);
  gl.uniform1i(shader.uniforms.modulation ?? null, 0);
  gl.uniform1f(shader.uniforms.scl_inter ?? null, hdr.scl_inter ?? 0);
  gl.uniform1f(shader.uniforms.scl_slope ?? null, hdr.scl_slope ?? 1);
  gl.uniform1f(shader.uniforms.opacity ?? null, volume.opacity ?? 1);
  gl.uniform1f(shader.uniforms.cal_min ?? null, volume.cal_min!);
  gl.uniform1f(shader.uniforms.cal_max ?? null, volume.cal_max!);
  gl.uniformMatrix4fv(shader.uniforms.mtx ?? null, false, mtx);
  gl.uniform4fv(shader.uniforms.xyzaFrac ?? null, [1 / backDims[1], 1 / backDims[2], 1 / backDims[3], 0]);
  configureColormapUniforms(gl, shader, volume, layer, Boolean(nv.opts.isAdditiveBlend));

  for (let z = 0; z < backDims[3]; z += 1) {
    gl.uniform1f(shader.uniforms.coordZ ?? null, (z + 0.5) / backDims[3]);
    gl.framebufferTextureLayer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, nv.volumeTexture, 0, z);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  gl.bindVertexArray(nv.unusedVAO ?? null);
  if (blendTexture) gl.deleteTexture(blendTexture);
  gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.deleteFramebuffer(framebuffer);
  nv.updateInterpolation?.(layer);
  logNiivueWindowingPerf('fast refresh', performance.now() - start, {
    id: volume.id,
    name: volume.name,
    layer,
    dims: backDims.slice(1, 4).join(':'),
  });
  return true;
}

export function refreshNiivueWindowingLayer(nvSource: Niivue, volume: NiivueVolumeInterop, details: Record<string, unknown> = {}): boolean {
  const nv = asNiivueInterop(nvSource);
  const refreshLayers = originalRefreshLayers.get(nv) ?? nv.refreshLayers;
  if (typeof refreshLayers !== 'function') return false;

  const layer = refreshLayerOfVolume(nv, volume);
  if (layer === null) return false;

  if (refreshNiivueWindowingOnly(nvSource, volume, layer)) return true;

  const fallbackStart = performance.now();
  refreshLayers.call(nvSource, volume, layer);
  logNiivueWindowingPerf('fallback refreshLayers', performance.now() - fallbackStart, {
    id: volume.id,
    name: volume.name,
    layer,
    ...details,
  });
  return true;
}

export function refreshNiivueLayerStack(nvSource: Niivue, volumes: Iterable<NiivueVolumeInterop>, details: Record<string, unknown> = {}): boolean {
  const nv = asNiivueInterop(nvSource);
  const refreshLayers = originalRefreshLayers.get(nv) ?? nv.refreshLayers;
  if (typeof refreshLayers !== 'function') return false;

  let firstLayer = Number.POSITIVE_INFINITY;
  const changedIds: string[] = [];
  for (const volume of volumes) {
    const layer = refreshLayerOfVolume(nv, volume);
    if (layer === null) continue;
    firstLayer = Math.min(firstLayer, layer);
    changedIds.push(volume.id ?? volume.name ?? String(layer));
  }
  if (!Number.isFinite(firstLayer)) return false;

  const startLayer = firstLayer > 1 ? firstLayer - 1 : firstLayer;
  const start = performance.now();
  let layer = 0;
  let refreshed = 0;
  for (const volume of nv.volumes) {
    if (!volume.toRAS) continue;
    if (layer >= startLayer) {
      refreshLayers.call(nvSource, volume, layer);
      refreshed += 1;
    }
    layer += 1;
  }
  logNiivueWindowingPerf('stack refreshLayers', performance.now() - start, {
    firstLayer,
    startLayer,
    changedIds,
    refreshed,
    ...details,
  });
  return refreshed > 0;
}

export function refreshNiivueWindowingOrLayerStack(nvSource: Niivue, volumes: Iterable<NiivueVolumeInterop>, details: Record<string, unknown> = {}): boolean {
  const volumeList = Array.from(volumes);
  if (volumeList.length === 0) return false;

  const nv = asNiivueInterop(nvSource);
  const layers = volumeList
    .map((volume) => refreshLayerOfVolume(nv, volume))
    .filter((layer): layer is number => layer !== null);
  if (layers.length === 0) return false;

  if (layers.every((layer) => layer === 0)) {
    let refreshed = false;
    for (const volume of volumeList) {
      refreshed = refreshNiivueWindowingLayer(nvSource, volume, details) || refreshed;
    }
    return refreshed;
  }

  return refreshNiivueLayerStack(nvSource, volumeList, details);
}

export function installNiivueWindowingRefreshPatch(nvSource: Niivue): void {
  const nv = asNiivueInterop(nvSource);
  if (patchedWindowingInstances.has(nv)) return;
  if (typeof nv.refreshLayers !== 'function') return;

  patchedWindowingInstances.add(nv);
  const refreshLayers = nv.refreshLayers.bind(nvSource);
  originalRefreshLayers.set(nv, refreshLayers);

  let pendingWindowingVolume: NiivueVolumeInterop | null = null;
  const originalCalculateNewRange = typeof nv.calculateNewRange === 'function'
    ? nv.calculateNewRange.bind(nvSource)
    : null;
  const originalWindowingHandler = typeof nv.windowingHandler === 'function'
    ? nv.windowingHandler.bind(nvSource)
    : null;

  if (originalCalculateNewRange) {
    nv.calculateNewRange = (options?: { volIdx?: number }) => {
      const volume = nv.volumes[options?.volIdx ?? 0] ?? null;
      const beforeMin = volume?.cal_min;
      const beforeMax = volume?.cal_max;
      originalCalculateNewRange(options);
      if (volume && (volume.cal_min !== beforeMin || volume.cal_max !== beforeMax)) {
        pendingWindowingVolume = volume;
      }
    };
  }

  if (originalWindowingHandler) {
    nv.windowingHandler = (x: number, y: number, volIdx = 0) => {
      const volume = nv.volumes[volIdx] ?? null;
      pendingWindowingVolume = volume;
      try {
        originalWindowingHandler(x, y, volIdx);
      } finally {
        pendingWindowingVolume = null;
      }
    };
  }

  nv.refreshLayers = (volume: NiivueVolumeInterop, layer: number) => {
    if (layer === 0 && pendingWindowingVolume === volume && refreshNiivueWindowingOnly(nvSource, volume, layer)) {
      pendingWindowingVolume = null;
      nv.drawScene?.();
      return;
    }
    if (pendingWindowingVolume === volume) pendingWindowingVolume = null;
    refreshLayers(volume, layer);
  };
}

export function scheduleNiivueWindowingTexturePrime(nvSource: Niivue, volume: NiivueVolumeInterop): void {
  const prime = () => {
    const start = performance.now();
    const texture = ensureRawTexture(asNiivueInterop(nvSource), volume);
    logNiivueWindowingPerf('cache prime', performance.now() - start, {
      id: volume.id,
      name: volume.name,
      cached: Boolean(texture),
    }, SLOW_TEXTURE_UPLOAD_MS);
  };
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(prime, { timeout: 1500 });
  } else {
    globalThis.setTimeout(prime, 0);
  }
}
