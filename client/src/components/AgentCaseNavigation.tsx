import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router';
import { appJson, jsonRequest } from '../utils/api';
import { createGuiSessionId } from '../utils/guiSession';

export function AgentCaseNavigation() {
  const navigate = useNavigate();
  const { workspaceId } = useParams();
  useEffect(() => {
    let stopped = false;
    let inFlight = false;
    const sessionId = createGuiSessionId();
    const ackKey = 'neurocade.navigation-ack';
    async function poll() {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await appJson<{ command: { id: string; case_id: string; workspace_id: string } | null }>(
          '/gui/navigation', 'Could not check case navigation', jsonRequest({
            browser_session_id: sessionId, workspace_id: workspaceId ?? null,
            acknowledged: sessionStorage.getItem(ackKey),
          }, { method: 'POST' }));
        if (!stopped && response.command && response.command.id !== sessionStorage.getItem(ackKey)) {
          sessionStorage.setItem(ackKey, response.command.id);
          await navigate(`/workspaces/${encodeURIComponent(response.command.workspace_id)}/cases/${encodeURIComponent(response.command.case_id)}`);
        }
      } catch { /* Retry on the next poll; navigation never blocks the workspace. */ }
      finally { inFlight = false; }
    }
    void poll();
    const timer = setInterval(() => { void poll(); }, 2000);
    return () => { stopped = true; clearInterval(timer); };
  }, [navigate, workspaceId]);
  return null;
}
