import assert from 'node:assert/strict';
import test from 'node:test';

import { caseRouteSlug, caseSummaryPath, caseViewerPath, workspaceCasesPath } from '../src/utils/caseRoutes.js';

void test('workspaceCasesPath encodes workspace ids', () => {
  assert.equal(workspaceCasesPath('workspace 1'), '/workspaces/workspace%201/cases');
});

void test('caseRouteSlug prefers the canonical case id suffix', () => {
  assert.equal(caseRouteSlug('workspace-1', 'workspace-1__subject-a', 'Display Name'), 'subject-a');
});

void test('caseRouteSlug falls back to title for non-canonical ids', () => {
  assert.equal(caseRouteSlug('workspace-1', 'external-id', 'Display Name'), 'Display Name');
});

void test('caseViewerPath encodes the case slug', () => {
  assert.equal(caseViewerPath('workspace-1', 'workspace-1__Subject A'), '/workspaces/workspace-1/cases/Subject%20A');
});

void test('caseSummaryPath returns null without a workspace id', () => {
  assert.equal(caseSummaryPath({ case_id: 'case-1', subject_name: 'Case 1' }), null);
});
