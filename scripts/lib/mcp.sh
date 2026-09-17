#!/usr/bin/env bash
# Query the actual launched endpoint, never a guessed port.
mcp_status() {
  [[ -s "$APP_URL_FILE" ]] || { echo "Local agent access: unavailable"; return; }
  "$BRIDGE_VENV/bin/python" - "$APP_URL_FILE" <<'PYCODE'
import json,sys,urllib.request
try:
    url=open(sys.argv[1]).read().strip()
    data=json.load(urllib.request.urlopen(url+'/api/app/healthz',timeout=3))
    print('Local agent access: '+('enabled ('+data.get('mcp_access','')+')' if data.get('mcp_enabled') else 'disabled'))
except Exception:
    print('Local agent access: unavailable')
PYCODE
}
mcp_check_existing() {
  [[ -s "$APP_URL_FILE" ]] || {
    echo "The configured NeuroCade port answered, but this install has no recorded app URL. Another instance or service may already be using the port." >&2
    return 1
  }
  "$BRIDGE_VENV/bin/python" - "$APP_URL_FILE" "$MCP_ENABLED" "$MCP_ACCESS" <<'PYCODE'
import json,sys,urllib.request
url=open(sys.argv[1]).read().strip()
data=json.load(urllib.request.urlopen(url+'/api/app/healthz',timeout=3))
requested=sys.argv[2]=='true'
if bool(data.get('mcp_enabled')) != requested or (requested and data.get('mcp_access') != sys.argv[3]):
    sys.exit('MCP settings differ from the running app. Stop and restart NeuroCade when active work permits.')
PYCODE
}
publish_mcp_descriptor() {
  [[ "$MCP_ENABLED" == true ]] || return 0
  "$BRIDGE_VENV/bin/python" - "$APP_URL_FILE" "$RUNTIME_DIR/mcp.json" "$LAUNCH_ID" <<'PYCODE'
import json,os,pathlib,sys,time,urllib.request
url=pathlib.Path(sys.argv[1]).read_text().strip()
target=pathlib.Path(sys.argv[2])
for attempt in range(120):
    try:
        data=json.load(urllib.request.urlopen(url+'/api/app/healthz',timeout=2))
        if data.get('mcp_enabled') and data.get('launch_id')==sys.argv[3]:
            temporary=target.with_suffix('.tmp')
            temporary.write_text(json.dumps({'url':url+'/mcp','launch_id':sys.argv[3],'access':data['mcp_access'],'authentication':'per-client bearer'}))
            os.replace(temporary,target)
            print('Connect local agents at '+url+'/local-agents',flush=True)
            break
    except Exception:
        pass
    time.sleep(1)
else:
    print('MCP did not become ready; check NeuroCade logs.',file=sys.stderr)
    sys.exit(1)
PYCODE
}

stop_mcp_discovery() {
  if [[ -n "${MCP_DISCOVERY_PID:-}" ]]; then
    kill "$MCP_DISCOVERY_PID" >/dev/null 2>&1 || true
    wait "$MCP_DISCOVERY_PID" 2>/dev/null || true
  fi
  rm -f "$RUNTIME_DIR/mcp.json"
}
