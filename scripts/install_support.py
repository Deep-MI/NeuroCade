#!/usr/bin/env python3
"""Small standard-library helpers for installer discovery and redacted diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward provider credentials to an unexpected endpoint.
        return None


def discover_models(provider: str, base: str, secret: str) -> list[str]:
    base = base.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Provide an http(s) endpoint without credentials in the URL.')
    headers = {'Accept': 'application/json'}
    if provider == 'anthropic':
        url = base + '/v1/models'
        headers.update({'x-api-key': secret, 'anthropic-version': '2023-06-01'})
    elif provider == 'google':
        url = base + '/v1beta/models'
        headers['x-goog-api-key'] = secret
    elif provider == 'ollama':
        url = base + '/api/tags'
    else:
        url = base + ('/models' if base.endswith('/v1') else '/v1/models')
        if secret:
            headers['Authorization'] = 'Bearer ' + secret
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=8) as response:
        payload = json.loads(response.read(2_000_001))
    if not isinstance(payload, dict):
        raise ValueError('Invalid model listing')
    items = payload.get('models' if provider in {'google', 'ollama'} else 'data', [])
    if not isinstance(items, list):
        raise ValueError('Invalid model listing')
    models = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if provider == 'google' and 'generateContent' not in item.get('supportedGenerationMethods', []):
            continue
        model = item.get('name' if provider in {'google', 'ollama'} else 'id')
        if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}', model):
            models.append(model.removeprefix('models/') if provider == 'google' else model)
    return sorted(set(models))[:50]


def secrets_from_env(path: Path) -> list[str]:
    values = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep:
                if value.startswith('"') and value.endswith('"'):
                    value = re.sub(r'\\([\\"$`])', r'\1', value[1:-1])
                values[key.strip()] = value
    return sorted({v for k, v in values.items() if v and re.search(r'KEY|TOKEN|SECRET|PASSWORD', k, re.I)}, key=len, reverse=True)


def redact(line: str, secrets: list[str]) -> str:
    for secret in secrets:
        line = line.replace(secret, '[REDACTED]')
    line = re.sub(r'(?i)(authorization:\s*(?:bearer|basic)\s+)\S+', r'\1[REDACTED]', line)
    line = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', line)
    return re.sub(r'(?i)([?&](?:key|token|api_key|access_token)=)[^&\s]+', r'\1[REDACTED]', line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    models = commands.add_parser('models')
    models.add_argument('--provider', choices=['anthropic', 'google', 'ollama', 'openai-compatible'], required=True)
    models.add_argument('--url', required=True)
    log = commands.add_parser('log')
    log.add_argument('--env-file', type=Path, required=True)
    log.add_argument('--log-file', type=Path, required=True)
    merge = commands.add_parser('merge-env')
    merge.add_argument('existing', type=Path)
    merge.add_argument('generated', type=Path)
    args = parser.parse_args()
    if args.command == 'merge-env':
        generated = args.generated.read_text()
        keys = {line.split('=', 1)[0] for line in generated.splitlines() if '=' in line}
        retained = [line for line in args.existing.read_text().splitlines()
                    if '=' in line and line.split('=', 1)[0].strip() not in keys]
        args.generated.write_text(generated + '\n'.join(retained) + '\n')
        return
    if args.command == 'models':
        try:
            print('\n'.join(discover_models(args.provider, args.url, sys.stdin.read())))
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            reason = f'HTTP {exc.code}' if isinstance(exc, HTTPError) else 'endpoint unavailable or unsupported'
            print(f'Model discovery failed ({reason}); enter a model name manually or keep the configured model.', file=sys.stderr)
    else:
        secrets = secrets_from_env(args.env_file)
        descriptor = os.open(args.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(args.log_file, 0o600)
        with os.fdopen(descriptor, 'a') as output:
            for line in sys.stdin:
                safe = redact(line, secrets)
                output.write(safe)
                output.flush()
                print(safe, end='', flush=True)


if __name__ == '__main__':
    main()
