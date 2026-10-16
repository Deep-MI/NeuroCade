"""Installer release contract, terminal interaction, and safe recovery without real jobs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / 'scripts/install.sh'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


support = module('install_support', 'scripts/install_support.py')
builder = module('build_installer', 'scripts/release/build_installer.py')
verifier = module('verify_installer', 'scripts/release/verify_installer.py')


def shell(code, *args, env=None, input=None):
    return subprocess.run(['bash', '-c', 'set -e; source "$1"; shift; ' + code, 'test', str(INSTALL), *map(str, args)],
                          env=env, input=input, capture_output=True, text=True, timeout=20)


def test_noninteractive_choice_and_model_do_not_read_stdin():
    result = shell('ASSUME_YES=1; choose "Provider" google "no-llm|Later" "google|Google"; choose_model google Model existing "https://unused.invalid" secret', input='ignored\n')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['google', 'existing']
    missing = shell('ASSUME_YES=1; choose_model google Model "" "https://unused.invalid" secret')
    assert missing.returncode != 0
    assert 'required' in missing.stderr


@pytest.mark.parametrize('terminal,keys,expected', [('dumb', b'9\n2\n', 'google'), ('xterm', b'\x1b[B\n', 'google'), ('xterm', b'q', None)])
def test_menu_terminal_navigation(terminal, keys, expected):
    master, slave = pty.openpty()
    env = {**os.environ, 'TERM': terminal}
    process = subprocess.Popen(['bash', '-c', 'source "$1"; choose Provider no-llm "no-llm|Later" "google|Google"', 'test', str(INSTALL)],
                               stdin=slave, stdout=subprocess.PIPE, stderr=slave, env=env)
    os.close(slave)
    try:
        deadline = time.monotonic() + 10
        transcript = b''
        while b'cancel:' not in transcript and time.monotonic() < deadline:
            if select.select([master], [], [], .2)[0]:
                transcript += os.read(master, 8192)
        assert b'cancel:' in transcript
        os.write(master, keys)
        output, _ = process.communicate(timeout=5)
        assert process.returncode == (0 if expected else 130)
        if expected:
            assert output.decode().strip() == expected
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def release_fixture(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    source = tmp_path / 'source/NeuroCade-test'
    (source / 'scripts/lib').mkdir(parents=True)
    (source / 'scripts/release').mkdir()
    for filename in ['install.sh', 'install_support.py']:
        shutil.copy2(ROOT / 'scripts' / filename, source / 'scripts' / filename)
    for filename in ['runtime_selection.sh', 'docker_cli.sh', 'env.sh', 'provenance.sh', 'apptainer_artifacts.sh', 'install_preflight.sh']:
        shutil.copy2(ROOT / 'scripts/lib' / filename, source / 'scripts/lib' / filename)
    shutil.copy2(ROOT / 'scripts/release/release_manifest.py', source / 'scripts/release/release_manifest.py')
    (source / 'scripts/lib/managed_python.sh').write_text(f'''NEUROCADE_PYTHON_VERSION=3.12
install_managed_uv() {{ :; }}
managed_uv() {{ :; }}
managed_python_path() {{ printf '%s\\n' '{sys.executable}'; }}
neurocade_host_arch() {{ echo x86_64; }}
''')
    (source / 'scripts/run.sh').write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$INSTALL_CALLS"\n')
    (source / 'scripts/run.sh').chmod(0o755)
    (source / 'scripts/update_source.py').write_text('pass\n')
    (source / 'scripts/update.sh').write_text('#!/usr/bin/env bash\nprintf "updated\\n" >>"$INSTALL_CALLS"\n')
    archive = assets / 'neurocade-source-1.2.3.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        output.add(source, arcname=source.name)
    (assets / (archive.name + '.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
    manifest = {'schema_version': 1, 'tag': 'v1.2.3', 'version': '1.2.3', 'architecture': 'amd64',
                'source_revision': 'a' * 40, 'minimum_updater_version': 1,
                'source_archive': {'filename': archive.name, 'sha256_filename': archive.name + '.sha256'},
                'application_sif': {'filename': 'app.sif', 'sha256_filename': 'app.sif.sha256'},
                'runtime_bridge': {'filename': 'bridge.whl', 'sha256_filename': 'bridge.whl.sha256'},
                'installer': {'filename': 'neurocade-install.sh', 'sha256_filename': 'neurocade-install.sh.sha256'}}
    (assets / 'neurocade-release.json').write_text(json.dumps(manifest))
    installer = assets / 'neurocade-install.sh'
    builder.build(INSTALL, installer, 'v1.2.3')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    # No real downloads, Docker calls, or installation outside this fixture.
    (bin_dir / 'curl').write_text('''#!/usr/bin/env bash
while (( $# )); do
  case "$1" in -o) target="$2"; shift 2;; *://*) url="$1"; shift;; *) shift;; esac
done
printf '%s\\n' "$url" >>"$DOWNLOADS"
[[ "${FAIL_DOWNLOAD:-}" != 1 ]] || { echo partial >"$target"; exit 7; }
cp "$ASSETS/${url##*/}" "$target"
''')
    (bin_dir / 'docker').write_text('#!/usr/bin/env bash\n[[ "${DOCKER_DOWN:-}" != 1 ]]\n')
    for p in bin_dir.iterdir():
        p.chmod(0o755)
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'ASSETS': str(assets),
           'INSTALL_CALLS': str(tmp_path / 'calls'), 'DOWNLOADS': str(tmp_path / 'downloads'),
           'NEUROCADE_INSTALL_DIR': str(tmp_path / 'installed'), 'NEUROCADE_MIN_FREE_KB': '0'}
    for key in list(env):
        if key.startswith('NEUROCADE_INSTALL_') and key != 'NEUROCADE_INSTALL_DIR':
            del env[key]
    return installer, assets, env


@pytest.mark.parametrize('arguments', [[], ['--version', 'v1.2.3'], ['--version=stable']])
def test_generated_installer_pins_docker_source_and_runs_real_setup(tmp_path, arguments):
    installer, _, env = release_fixture(tmp_path)
    result = subprocess.run(['bash', str(installer), '--yes', '--runtime', 'docker', '--llm-provider', 'no-llm', *arguments], env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    root = Path(env['NEUROCADE_INSTALL_DIR'])
    assert 'NEUROCADE_VERSION="1.2.3"' in (root / '.env').read_text()
    assert Path(env['INSTALL_CALLS']).read_text().splitlines() == ['build', 'start -d']
    downloads = Path(env['DOWNLOADS']).read_text()
    if not arguments:
        assert '/download/v1.2.3/neurocade-release.json' in downloads
        assert '/latest/' not in downloads
    assert '/download/v1.2.3/neurocade-source-1.2.3.tar.gz' in downloads
    assert (root / '.runtime/release/neurocade-release.json').exists()


@pytest.mark.parametrize('failure', ['download', 'checksum', 'tag'])
def test_failed_bootstrap_is_retryable_without_partial_install(tmp_path, failure):
    installer, assets, env = release_fixture(tmp_path)
    checksum = assets / 'neurocade-source-1.2.3.tar.gz.sha256'
    original = checksum.read_text()
    manifest = assets / 'neurocade-release.json'
    original_manifest = manifest.read_text()
    if failure == 'download':
        env['FAIL_DOWNLOAD'] = '1'
    elif failure == 'checksum':
        checksum.write_text('0' * 64 + '  neurocade-source-1.2.3.tar.gz\n')
    else:
        manifest.write_text(original_manifest.replace('v1.2.3', 'v9.9.9'))
    command = ['bash', str(installer), '--yes', '--runtime', 'docker', '--llm-provider', 'no-llm']
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert not Path(env['NEUROCADE_INSTALL_DIR']).exists()
    assert not Path(env['INSTALL_CALLS']).exists()
    assert 'Traceback' not in result.stderr
    env.pop('FAIL_DOWNLOAD', None)
    checksum.write_text(original)
    manifest.write_text(original_manifest)
    retry = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert retry.returncode == 0, retry.stderr


def test_docker_engine_failure_precedes_build_and_config_write(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    env['DOCKER_DOWN'] = '1'
    result = subprocess.run(['bash', str(installer), '--yes', '--runtime', 'docker'], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert 'engine is not responding' in result.stderr
    assert not Path(env['INSTALL_CALLS']).exists()
    assert not (Path(env['NEUROCADE_INSTALL_DIR']) / '.env').exists()


def test_downloaded_installer_verification_rejects_tampering(tmp_path):
    installer, assets, _ = release_fixture(tmp_path)
    verifier.verify(assets, 'v1.2.3')
    installer.write_text(installer.read_text() + '\n# changed\n')
    with pytest.raises(ValueError, match='checksum'):
        verifier.verify(assets, 'v1.2.3')


def test_log_redacts_env_keys_headers_and_url_credentials(tmp_path):
    env = tmp_path / '.env'
    env.write_text('GOOGLE_API_KEY="private$token"\n')
    values = support.secrets_from_env(env)
    text = support.redact('private$token Authorization: Bearer unknown https://user:pass@host/?key=secret', values)
    assert all(secret not in text for secret in ['private$token', 'unknown', 'user:pass', '=secret'])


def test_model_discovery_uses_ids_and_keeps_keys_out_of_urls(monkeypatch):
    from io import BytesIO
    captured = []
    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 8
            return BytesIO(json.dumps({'data': [{'id': 'model-b'}, {'id': 'model-a'}, {'id': '\x1b[31m'}]}).encode())
    monkeypatch.setattr(support, 'build_opener', lambda *args: Opener())
    assert support.discover_models('openai-compatible', 'http://localhost:1234/v1', 'test-key') == ['model-a', 'model-b']
    assert captured[0].full_url == 'http://localhost:1234/v1/models'
    assert captured[0].headers['Authorization'] == 'Bearer test-key'


def test_engine_failure_can_resume_same_install(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    command = ['bash', str(installer), '--yes', '--runtime', 'docker']
    failed = subprocess.run(command, env={**env, 'DOCKER_DOWN': '1'}, capture_output=True, text=True, timeout=20)
    assert failed.returncode != 0
    root = Path(env['NEUROCADE_INSTALL_DIR'])
    assert (root / '.runtime/install-incomplete').exists()
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Resuming incomplete installation' in result.stdout
    assert not (root / '.runtime/install-incomplete').exists()


def test_yes_suppresses_directory_prompt_even_on_tty(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    master, slave = pty.openpty()
    process = subprocess.Popen(['bash', str(installer), '--yes', '--runtime', 'docker'], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    transcript = b''
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                try:
                    transcript += os.read(master, 65536)
                except OSError:
                    break
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, transcript.decode()
        assert b'Install directory [' not in transcript
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def test_missing_unattended_provider_settings_fail_before_build(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    env.pop('ANTHROPIC_API_KEY', None)
    env.pop('ANTHROPIC_MODEL', None)
    result = subprocess.run(['bash', str(installer), '--yes', '--runtime', 'docker', '--llm-provider', 'anthropic'], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert 'ANTHROPIC_API_KEY' in result.stderr
    assert not Path(env['INSTALL_CALLS']).exists()


def test_reconfigure_keeps_custom_settings_and_port(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    result = subprocess.run(['bash', str(installer), '--yes', '--runtime', 'docker'], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    root = Path(env['NEUROCADE_INSTALL_DIR'])
    config = root / '.env'
    config.write_text(config.read_text().replace('NEUROCADE_BRIDGE_PORT="8765"', 'NEUROCADE_BRIDGE_PORT="8877"') + 'CUSTOM_OPTION="keep-me"\n')
    result = subprocess.run(['bash', str(root / 'scripts/install.sh'), '--yes'], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'NEUROCADE_BRIDGE_PORT="8877"' in config.read_text()
    assert 'CUSTOM_OPTION="keep-me"' in config.read_text()


def test_apptainer_uses_same_resolved_manifest(tmp_path):
    installer, assets, env = release_fixture(tmp_path)
    bin_dir = tmp_path / 'bin'
    (bin_dir / 'uname').write_text('#!/usr/bin/env bash\n[[ "$1" == -s ]] && echo Linux || echo x86_64\n')
    (bin_dir / 'id').write_text('#!/usr/bin/env bash\necho 1000\n')
    (bin_dir / 'apptainer').write_text('#!/usr/bin/env bash\necho --no-home\n')
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)
    namespaces = tmp_path / 'namespaces'
    namespaces.write_text('1024\n')
    env['USER_NAMESPACE_FILE'] = str(namespaces)
    for name in ['app.sif', 'bridge.whl']:
        (assets / name).write_bytes(b'release artifact')
        (assets / (name + '.sha256')).write_text(hashlib.sha256(b'release artifact').hexdigest() + '  ' + name + '\n')
    result = subprocess.run(['bash', str(installer), '--yes', '--runtime', 'apptainer'], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert Path(env['DOWNLOADS']).read_text().count('neurocade-release.json') == 1
    root = Path(env['NEUROCADE_INSTALL_DIR'])
    assert (root / '.runtime/images/neurocade-app-amd64.sif').read_bytes() == b'release artifact'
    assert 'NEUROCADE_RELEASE_VERSION="1.2.3"' in (root / '.env').read_text()


def test_noninteractive_reconfigure_does_not_change_release(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    command = ['bash', str(installer), '--yes', '--runtime', 'docker']
    assert subprocess.run(command, env=env, capture_output=True, timeout=20).returncode == 0
    result = subprocess.run(['bash', str(installer), '--yes', '--reconfigure'], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'NEUROCADE_VERSION="1.2.3"' in (Path(env['NEUROCADE_INSTALL_DIR']) / '.env').read_text()


def test_build_failure_is_redacted_and_resumable(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    # First fail early, leaving the exact extracted installer ready for retry.
    command = ['bash', str(installer), '--yes', '--runtime', 'docker']
    subprocess.run(command, env={**env, 'DOCKER_DOWN': '1'}, capture_output=True, timeout=20)
    root = Path(env['NEUROCADE_INSTALL_DIR'])
    runner = root / 'scripts/run.sh'
    original = runner.read_text()
    runner.write_text('#!/usr/bin/env bash\necho "build failure $SECRET_TEST_TOKEN"\nexit 17\n')
    result = subprocess.run(command, env={**env, 'SECRET_TEST_TOKEN': 'test-private-value'}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 17
    assert 'Building application image' in result.stderr
    assert 'test-private-value' not in result.stdout + result.stderr
    log = root / '.runtime/install.log'
    assert '[REDACTED]' in log.read_text()
    assert log.stat().st_mode & 0o777 == 0o600
    runner.write_text(original)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('reason', ['architecture', 'disk', 'permissions'])
def test_preflight_explains_host_failure_before_work(tmp_path, reason):
    root = tmp_path / 'install'
    root.mkdir()
    overrides = 'neurocade_host_arch() { echo x86_64; }; uname() { echo Linux; }; docker() { return 0; };'
    if reason == 'architecture':
        overrides += 'neurocade_host_arch() { echo aarch64; };'
    elif reason == 'disk':
        overrides += 'NEUROCADE_MIN_FREE_KB=999999999999;'
    else:
        root.chmod(0o500)
    try:
        result = shell(overrides + 'source "$1"; install_preflight "$2" docker', ROOT / 'scripts/lib/install_preflight.sh', root)
        assert result.returncode != 0
        expected = {'architecture': 'Unsupported host', 'disk': 'Insufficient free space', 'permissions': 'not writable'}
        assert expected[reason] in result.stderr
        assert not (root / '.runtime').exists()
    finally:
        root.chmod(0o700)


def test_invalid_release_metadata_fails_without_traceback(tmp_path):
    installer, assets, env = release_fixture(tmp_path)
    (assets / 'neurocade-release.json').write_text('{broken-json')
    result = subprocess.run(['bash', str(installer), '--yes'], env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert 'Release metadata is invalid' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not Path(env['NEUROCADE_INSTALL_DIR']).exists()


def test_repeating_website_command_preserves_existing_settings(tmp_path):
    installer, _, env = release_fixture(tmp_path)
    command = ['bash', str(installer), '--yes', '--runtime', 'docker', '--mode', 'local', '--llm-provider', 'no-llm']
    first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert first.returncode == 0, first.stderr
    repeated = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 0, repeated.stderr
    assert Path(env['INSTALL_CALLS']).read_text().splitlines()[-1] == 'updated'
    changed = subprocess.run(['bash', str(installer), '--yes', '--runtime', 'apptainer'], env=env, capture_output=True, text=True, timeout=20)
    assert changed.returncode != 0
    assert 'cannot change --runtime' in changed.stderr
