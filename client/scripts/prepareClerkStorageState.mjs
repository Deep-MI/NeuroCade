import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from '@playwright/test';
import { clerk, clerkSetup } from '@clerk/testing/playwright';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const clientDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(clientDir, '..');

for (const envPath of [
  path.join(repoRoot, '.env.local'),
  path.join(repoRoot, '.env'),
  path.join(clientDir, '.env.local'),
  path.join(clientDir, '.env'),
]) {
  try {
    const contents = await fs.readFile(envPath, 'utf8');
    for (const rawLine of contents.split('\n')) {
      const line = rawLine.trim();
      if (!line || line.startsWith('#')) {
        continue;
      }
      const separatorIndex = line.indexOf('=');
      if (separatorIndex <= 0) {
        continue;
      }
      const key = line.slice(0, separatorIndex).trim();
      const value = line.slice(separatorIndex + 1).trim().replace(/^['"]|['"]$/g, '');
      if (!(key in process.env)) {
        process.env[key] = value;
      }
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      throw error;
    }
  }
}

const appUrl = process.env.APP_URL ?? 'http://localhost:8000';
const storageStatePath = path.resolve(
  process.env.CLERK_STORAGE_STATE_PATH ?? path.join(repoRoot, 'playwright', '.clerk', 'user.json'),
);
const testEmail = process.env.CLERK_TEST_EMAIL;

if (!testEmail) {
  throw new Error('CLERK_TEST_EMAIL is required to generate Clerk storage state.');
}

await fs.mkdir(path.dirname(storageStatePath), { recursive: true });
await clerkSetup();

const browser = await chromium.launch({
  headless: process.env.HEADED?.toLowerCase() !== '1' && process.env.HEADED?.toLowerCase() !== 'true',
});

try {
  const context = await browser.newContext({ ignore_https_errors: true });
  const page = await context.newPage();

  await page.goto(`${appUrl}/sign-in`, { waitUntil: 'networkidle', timeout: 60_000 });
  await clerk.signIn({ page, emailAddress: testEmail });
  await page.goto(`${appUrl}/`, { waitUntil: 'networkidle', timeout: 60_000 });
  await page.waitForURL(url => !url.pathname.startsWith('/sign-in') && !url.pathname.startsWith('/sign-up'), { timeout: 60_000 });
  await context.storageState({ path: storageStatePath });

  console.log(`Saved Clerk storage state to ${storageStatePath}`);
} finally {
  await browser.close();
}
