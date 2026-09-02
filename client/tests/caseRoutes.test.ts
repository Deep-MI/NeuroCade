import assert from 'node:assert/strict';
import test from 'node:test';

import { caseSummaryPath, caseViewerPath, workspaceCasesPath } from '../src/utils/caseRoutes.js';

void test('case route helpers use canonical encoded paths', () => {
  assert.equal(workspaceCasesPath('workspace 1'), '/workspaces/workspace%201/cases');
  assert.equal(caseViewerPath('workspace-1', 'case id'), '/workspaces/workspace-1/cases/case%20id');
  assert.equal(
    caseSummaryPath({
      workspace_id: 'workspace-1',
      id: 'case-id',
    }),
    '/workspaces/workspace-1/cases/case-id',
  );
});
