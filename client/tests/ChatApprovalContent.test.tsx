import assert from 'node:assert/strict';
import test from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { ChatApprovalContent } from '../src/components/ChatApprovalContent.js';
import type { AssistantApprovalRequest } from '../src/types.js';

void test('renders a bounded fallback when structured approval details are unavailable', () => {
  const approval = {
    name: 'tool_call',
    arguments: {},
    digest: 'a'.repeat(64),
    description: 'run the selected workflow',
  } as AssistantApprovalRequest;

  const markup = renderToStaticMarkup(<ChatApprovalContent approval={approval} />);

  assert.match(markup, /Action requires confirmation/);
  assert.match(markup, /run the selected workflow/);
});
