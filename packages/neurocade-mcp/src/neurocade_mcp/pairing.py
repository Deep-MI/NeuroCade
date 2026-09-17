"""Redeem local setup grants and privately cache credentials for every adapter."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def validate_pairing(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid pairing. Create a new setup prompt or extension in NeuroCade.")
    url = urlsplit(data.get("url", ""))
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.path != "/mcp" or url.username or url.password or url.query or url.fragment):
        raise ValueError("Pairing requires a local NeuroCade endpoint.")
    for key in ("pairing_id", "installation_id", "code"):
        if not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 128:
            raise ValueError("Invalid pairing. Create a new setup prompt or extension in NeuroCade.")
    return data


def paired_connection(data, directory=None):
    from neurocade_mcp import load_connection
    from neurocade_mcp.setup import private_json

    data = validate_pairing(data)
    directory = Path(directory).expanduser() if directory else Path.home() / ".local/share/neurocade/connections"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError("NeuroCade credentials directory must be owned by you and not a symbolic link.")
    slot = hashlib.sha256((data['installation_id'] + ':' + data['pairing_id']).encode()).hexdigest()
    path = directory / (slot + '.json')
    # Multiple extension launches must not race to redeem the same grant.
    fd = os.open(directory / (slot + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if path.exists() or path.is_symlink():
            saved = load_connection(path)
            if saved['installation_id'] != data['installation_id'] or saved['url'] != data['url']:
                raise ValueError("Saved pairing belongs to a different installation. Pair again in NeuroCade.")
            return path
        parsed = urlsplit(data['url'])
        endpoint = parsed._replace(path='/api/app/mcp/pairings/redeem').geturl()
        try:
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
                response = client.post(endpoint, json={key: data[key] for key in ('pairing_id', 'installation_id', 'code')},
                                       headers={'X-NeuroCade-UI': '1'})
        except httpx.TransportError:
            raise ValueError("Pairing connection interrupted. Restart the connector; if the code was already used, generate a new setup prompt or extension in NeuroCade.") from None
        if response.status_code == 410:
            raise ValueError("Pairing expired or was already used. Generate a new setup prompt or extension in NeuroCade.")
        if response.status_code == 409:
            raise ValueError("Different NeuroCade installation. Generate a new setup prompt or extension from the running app.")
        if response.status_code != 200:
            raise ValueError("Pairing was rejected. Check workspace access and local agents in NeuroCade, then generate a new setup prompt or extension.")
        saved = response.json()
        if saved.get('installation_id') != data['installation_id'] or saved.get('url') != data['url']:
            raise ValueError("Pairing response does not match the selected installation.")
        if not isinstance(saved.get('token'), str) or not saved['token'].startswith('ncmcp_') or not saved.get('client_id'):
            raise ValueError("Invalid pairing response. Generate a new setup prompt or extension.")
        private_json(path, saved)
        load_connection(path)
        return path
    finally:
        os.close(fd)


def read_pairing(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Invalid pairing package. Download the extension again.")
    return validate_pairing(json.loads(path.read_text()))
