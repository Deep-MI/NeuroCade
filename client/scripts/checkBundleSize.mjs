import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';

const assetsDir = new URL('../dist/assets/', import.meta.url);
const niivueFiles = (await readdir(assetsDir)).filter((name) => /^niivue-.*\.js$/.test(name));
if (niivueFiles.length !== 1) {
  throw new Error(`Expected one lazy NiiVue bundle, found ${niivueFiles.length}`);
}
const bundlePath = join(assetsDir.pathname, niivueFiles[0]);
const size = (await stat(bundlePath)).size;
const limit = 1_500_000;
if (size > limit) {
  throw new Error(`NiiVue bundle is ${size} bytes; regression limit is ${limit} bytes`);
}
console.log(`NiiVue bundle size: ${size}/${limit} bytes`);
