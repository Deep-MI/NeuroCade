import assert from 'node:assert/strict';
import test from 'node:test';

import { caseSummaryPath, caseViewerPath, workspaceCasesPath } from '../src/utils/caseRoutes.js';

void test('workspaceCasesPath encodes workspace ids', () => {
  assert.equal(workspaceCasesPath('workspace 1'), '/workspaces/workspace%201/cases');
});

void test('caseViewerPath encodes the immutable case id', () => {
  assert.equal(caseViewerPath('workspace-1', 'case id'), '/workspaces/workspace-1/cases/case%20id');
});

void test('caseSummaryPath uses the canonical API case shape', () => {
  assert.equal(
    caseSummaryPath({
      workspace_id: 'workspace-1',
      id: 'case-id',
    }),
    '/workspaces/workspace-1/cases/case-id',
  );
});
