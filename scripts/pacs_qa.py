"""Local-only Orthanc QA fixture: setup, seed six real MRI cases, serve OAuth/TLS.

Run with .venv/bin/python scripts/pacs_qa.py --help. Never use hospital data here.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pydicom
import requests
from fastapi import Request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime/pacs-qa"
ORTHANC = "http://127.0.0.1:8042"
BUCKET = "https://s3.amazonaws.com/idc-open-data"
MANIFEST = "https://raw.githubusercontent.com/FNNDSC/sample_dicom_downloader/8dc029f833331cc1238695a796a3058dfe9fa76c/s3manifests/ReMIND_BRAIN_MR/gcs.s5cmd"
CONTAINER = "neurocade-qa-pacs"
ORTHANC_IMAGE = "orthancteam/orthanc@sha256:9758c8702a89abece99fcfe6d5571d5eaae59587e8e1ce36b9aafc8d4f24457b"


def configuration():
    return json.loads((STATE / "credentials.json").read_text())


def docker(*args, capture=False):
    # Isolated anonymous configuration avoids changing the user's credential store.
    endpoint = os.environ.get("DOCKER_HOST")
    if not endpoint:
        endpoint = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                                  check=True, text=True, capture_output=True).stdout.strip()
    command = ["docker", "--config", str(STATE / "docker"), "--host", endpoint, *args]
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def setup():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    (STATE / "docker").mkdir(exist_ok=True)
    (STATE / "docker/config.json").write_text("{}\n")
    path = STATE / "credentials.json"
    if not path.exists():
        path.write_text(json.dumps({"username": "qa", "password": secrets.token_urlsafe(32),
                                    "client_id": "neurocade-qa", "client_secret": secrets.token_urlsafe(32)}))
        path.chmod(0o600)
    cfg = configuration()
    cert = STATE / "certificate.pem"
    if not cert.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                        "-keyout", str(STATE / "key.pem"), "-out", str(cert),
                        "-subj", "/CN=NeuroCade local QA PACS",
                        "-addext", "subjectAltName=DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (STATE / "key.pem").chmod(0o600)
    config_path = STATE / "orthanc.json"
    config_path.write_text(json.dumps({
        "Name": "NeuroCade QA - public ReMIND MRI only", "RemoteAccessAllowed": True,
        "AuthenticationEnabled": True, "RegisteredUsers": {cfg["username"]: cfg["password"]},
        "DicomServerEnabled": False, "StorageDirectory": "/var/lib/orthanc/db",
        "DicomWeb": {"Enable": True, "Root": "/dicom-web/"},
    }))
    config_path.chmod(0o600)
    existing = docker("ps", "-a", "--filter", f"name=^/{CONTAINER}$", "--format", "{{.Names}}", capture=True).stdout.strip()
    if existing:
        labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", CONTAINER, capture=True).stdout)
        if labels.get("org.neurocade.qa") != str(STATE):
            raise RuntimeError("Container name belongs to another setup; refusing to change it")
        docker("start", CONTAINER)
    else:
        image_file = STATE / "image.txt"
        if not image_file.exists():
            docker("pull", ORTHANC_IMAGE)
            image_file.write_text(ORTHANC_IMAGE + "\n")
        docker("run", "-d", "--name", CONTAINER, "--label", f"org.neurocade.qa={STATE}",
               "--restart", "unless-stopped", "--memory", "1g", "--cpus", "2",
               "-p", "127.0.0.1:8042:8042", "-e", "DICOM_WEB_PLUGIN_ENABLED=true",
               "-v", f"{config_path}:/etc/orthanc/orthanc.json:ro",
               "-v", "neurocade-qa-pacs-data:/var/lib/orthanc/db", image_file.read_text().strip())
    for _ in range(60):
        try:
            response = requests.get(ORTHANC + "/system", auth=(cfg["username"], cfg["password"]), timeout=2)
            if response.ok:
                print("Orthanc ready at http://127.0.0.1:8042", flush=True)
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("Orthanc did not become ready")


def objects(prefix):
    if not re.fullmatch(r"[0-9a-f-]{36}", prefix):
        raise ValueError("Invalid public series prefix")
    params = {"list-type": "2", "prefix": prefix + "/"}
    result = []
    while True:
        response = requests.get(BUCKET, params=params, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        result.extend((entry.findtext("s:Key", default="", namespaces=ns), int(entry.findtext("s:Size", default="0", namespaces=ns)))
                      for entry in root.findall("s:Contents", ns)
                      if int(entry.findtext("s:Size", default="0", namespaces=ns)) > 0)
        if root.findtext("s:IsTruncated", namespaces=ns) != "true":
            return result
        params["continuation-token"] = root.findtext("s:NextContinuationToken", default="", namespaces=ns)


def download(key, size):
    if (not key or key.startswith("/") or "\\" in key
            or any(part in ("", ".", "..") for part in key.split("/"))):
        raise ValueError("Unsafe public object key")
    if size > 64 * 1024 * 1024:
        raise ValueError("Unexpectedly large fixture instance")
    path = STATE / "downloads" / key
    if path.exists() and path.stat().st_size == size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    with requests.get(BUCKET + "/" + key, stream=True, timeout=(10, 60)) as response:
        response.raise_for_status()
        count = 0
        with temporary.open("wb") as stream:
            for chunk in response.iter_content(65536):
                count += len(chunk)
                if count > size:
                    raise ValueError("Download larger than manifest")
                stream.write(chunk)
    if count != size:
        raise ValueError("Incomplete fixture download")
    temporary.replace(path)
    return path


def peek_header(key):
    """Avoid downloading an entire segmentation merely to identify its modality."""
    with requests.get(BUCKET + "/" + key, headers={"Range": "bytes=0-524287"},
                      stream=True, timeout=(10, 30)) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) >= 524288:
                break
    return pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)


def seed():
    cfg = configuration()
    manifest_path = STATE / "upstream-manifest.s5cmd"
    if not manifest_path.exists():
        response = requests.get(MANIFEST, timeout=30)
        response.raise_for_status()
        manifest_path.write_text(response.text)
    inventory_path = STATE / "inventory.json"
    inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else []
    # Rehydrate an emptied QA archive from the recorded originals on repeated runs.
    for row in inventory:
        for item in row["files"]:
            path = STATE / "downloads" / item["key"]
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("Cached sample checksum mismatch")
            response = requests.post(ORTHANC + "/instances", data=data,
                                     auth=(cfg["username"], cfg["password"]), timeout=30)
            response.raise_for_status()
    patients = {item["patient_id"] for item in inventory}
    prefixes = [line.split("/")[3] for line in manifest_path.read_text().splitlines() if line.startswith("cp s3://")]
    for prefix in prefixes:
        if len(patients) >= 6:
            break
        entries = objects(prefix)
        if (not entries or sum(size for _, size in entries) > 250 * 1024 * 1024
                or max(size for _, size in entries) > 64 * 1024 * 1024):
            continue
        header = peek_header(entries[0][0])
        patient = str(header.PatientID)
        if patient in patients or header.Modality != "MR":
            continue
        print(f"Downloading distinct case {len(patients) + 1}/6: {patient}, {len(entries)} instances", flush=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(lambda entry: download(*entry), entries))
        hashes = []
        for path in paths:
            data = path.read_bytes()
            ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)
            if str(ds.PatientID) != patient or str(ds.SeriesInstanceUID) != str(header.SeriesInstanceUID):
                raise ValueError("Mixed patient/series in upstream manifest")
            response = requests.post(ORTHANC + "/instances", data=data,
                                     auth=(cfg["username"], cfg["password"]), timeout=30)
            response.raise_for_status()
            hashes.append({"key": str(path.relative_to(STATE / "downloads")), "sha256": hashlib.sha256(data).hexdigest()})
        inventory.append({"patient_id": patient, "study_uid": str(header.StudyInstanceUID),
                          "series_uid": str(header.SeriesInstanceUID), "study_date": str(header.get("StudyDate", "")),
                          "description": str(header.get("SeriesDescription", "")), "instances": len(paths),
                          "source": "https://doi.org/10.7937/3RAG-D070", "license": "CC BY 4.0",
                          "files": hashes})
        inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")
        patients.add(patient)
    if len(patients) != 6:
        raise RuntimeError(f"Expected six distinct patients; obtained {len(patients)}")
    print("Seeded six distinct ReMIND patients, one complete MRI series per patient.", flush=True)


def create_app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, StreamingResponse
    from starlette.background import BackgroundTask

    cfg = configuration()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    issued = {}
    basic = "Basic " + base64.b64encode(f'{cfg["client_id"]}:{cfg["client_secret"]}'.encode()).decode()

    @app.post("/token")
    async def token(request: Request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), basic):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        from urllib.parse import parse_qs
        body = await request.body()
        if len(body) > 4096 or parse_qs(body.decode()).get("grant_type") != ["client_credentials"]:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        now = time.monotonic()
        for key in list(issued):
            if issued[key] <= now:
                issued.pop(key)
        access = secrets.token_urlsafe(32)
        issued[access] = now + 300
        return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": 300},
                            headers={"Cache-Control": "no-store"})

    @app.get("/dicom-web/{path:path}")
    def proxy(path: str, request: Request):
        authorization = request.headers.get("authorization", "")
        access = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if issued.get(access, 0) <= time.monotonic():
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        if not path.startswith("studies") or ".." in path or "\\" in path:
            return JSONResponse({"error": "invalid_path"}, status_code=400)
        response = requests.get(ORTHANC + "/dicom-web/" + path, params=list(request.query_params.multi_items()),
                                headers={"Accept": request.headers.get("accept", "application/dicom+json")},
                                auth=(cfg["username"], cfg["password"]), timeout=(5, 60), stream=True,
                                allow_redirects=False)
        return StreamingResponse(response.iter_content(65536), status_code=response.status_code,
                                 headers={"Content-Type": response.headers.get("Content-Type", "application/octet-stream")},
                                 background=BackgroundTask(response.close))

    return app


def serve():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8443, ssl_keyfile=str(STATE / "key.pem"),
                ssl_certfile=str(STATE / "certificate.pem"), access_log=False)


def start_gateway():
    # Confirm this exact fixture is already responding before starting a process.
    try:
        response = requests.post("https://localhost:8443/token", verify=str(STATE / "certificate.pem"), timeout=2)
        if response.status_code == 401:
            print("QA gateway already running")
            return
        raise RuntimeError("Port 8443 is occupied by a different service")
    except requests.ConnectionError:
        pass
    with (STATE / "gateway.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    (STATE / "gateway.pid").write_text(str(process.pid))
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError("Gateway exited; inspect gateway.log")
        try:
            response = requests.post("https://localhost:8443/token", verify=str(STATE / "certificate.pem"), timeout=2)
            if response.status_code == 401:
                break
        except requests.ConnectionError:
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("Gateway readiness timeout; inspect gateway.log")
    print("QA HTTPS gateway started; log: " + str(STATE / "gateway.log"))


def verify():
    """Exercise the actual application adapter without modifying any NeuroCade cases."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "api-service"))
    from api_service.pacs.client import PacsClient, value

    from backend_common.settings import Settings

    cfg = configuration()
    settings = Settings(**{
        "_env_file": None, "pacs_base_url": "https://localhost:8443/dicom-web",
        "pacs_token_url": "https://localhost:8443/token", "pacs_client_id": cfg["client_id"],
        "pacs_client_secret": cfg["client_secret"], "pacs_ca_bundle": str(STATE / "certificate.pem"),
    })
    inventory = json.loads((STATE / "inventory.json").read_text())
    if len({row["patient_id"] for row in inventory}) != 6:
        raise RuntimeError("Fixture incomplete: expected six distinct patients")
    report = []
    client = PacsClient(settings)
    try:
        assert requests.get(settings.pacs_base_url + "/studies", verify=settings.pacs_ca_bundle, timeout=5).status_code == 401
        for row in inventory:
            studies = client.query("/studies", {"PatientID": row["patient_id"]})
            assert any(value(item, "0020000D") == row["study_uid"] for item in studies)
            instances = client.instances(row["study_uid"], row["series_uid"])
            assert len(instances) == row["instances"]
            instance = value(instances[0], "00080018")
            with tempfile.TemporaryDirectory(prefix="verify-", dir=STATE) as directory:
                path = Path(directory) / "image.dcm"
                size, digest = client.retrieve(row["study_uid"], row["series_uid"], instance, path, lambda count: None)
                header = pydicom.dcmread(path, stop_before_pixels=True)
                assert str(header.SOPInstanceUID) == instance
                assert str(header.PatientID) == row["patient_id"]
            report.append({"patient_id": row["patient_id"], "instances": len(instances),
                           "retrieval_bytes": size, "retrieval_sha256": digest})
            print(f'PASS {row["patient_id"]}: search, paginated instances, WADO retrieval', flush=True)
    finally:
        client.close()
    (STATE / "verification.json").write_text(json.dumps(report, indent=2) + "\n")


def export_env():
    cfg = configuration()
    content = "\n".join([
        "# QA fixture only. Set the exact workspace ID before enabling.",
        "PACS_ENABLED=false", "PACS_WORKSPACE_IDS=", "PACS_SOURCE_ID=local-remind-qa",
        "PACS_BASE_URL=https://localhost:8443/dicom-web", "PACS_TOKEN_URL=https://localhost:8443/token",
        f'PACS_CLIENT_ID={cfg["client_id"]}', f'PACS_CLIENT_SECRET={cfg["client_secret"]}',
        f"PACS_CA_BUNDLE={STATE / 'certificate.pem'}", "PACS_SCOPE=", "PACS_QIDO_PREFIX=", "PACS_WADO_PREFIX=", "",
    ])
    path = STATE / "neurocade.env"
    path.write_text(content)
    path.chmod(0o600)
    print("Connection settings written to " + str(path))


def status():
    cfg = configuration()
    response = requests.get(ORTHANC + "/statistics", auth=(cfg["username"], cfg["password"]), timeout=5)
    response.raise_for_status()
    statistics = response.json()
    print(json.dumps({key: statistics[key] for key in ["CountPatients", "CountStudies", "CountSeries", "CountInstances"]}))
    for row in json.loads((STATE / "inventory.json").read_text()):
        print(f'{row["patient_id"]}: {row["description"]} ({row["instances"]} instances)')


def configure_app():
    """Prepare an opt-in launcher overlay; leave the user's original .env intact."""
    from dotenv import dotenv_values

    response = requests.get("http://localhost:8000/api/app/workspaces", timeout=10)
    response.raise_for_status()
    matches = [w for w in response.json() if w["name"] == "pacs-qa"]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous QA workspace")
    if not matches:
        response = requests.post("http://localhost:8000/api/app/workspaces",
                                 json={"name": "pacs-qa", "description": "Public ReMIND PACS integration QA only"}, timeout=10)
        response.raise_for_status()
        matches = [response.json()]
    original = (ROOT / ".env").read_text()
    baseline = dotenv_values(ROOT / ".env")
    data_root = Path(baseline.get("HOST_DATA_DIR") or ROOT / "neurocade-data").expanduser()
    if not data_root.is_absolute():
        data_root = ROOT / data_root
    import shutil
    certificate_dir = data_root / "pacs-qa"
    certificate_dir.mkdir(exist_ok=True)
    shutil.copyfile(STATE / "certificate.pem", certificate_dir / "certificate.pem")
    cfg = configuration()
    overrides = {
        "PACS_ENABLED": "true", "PACS_WORKSPACE_IDS": matches[0]["id"], "PACS_SOURCE_ID": "local-remind-qa",
        "PACS_BASE_URL": "https://host.docker.internal:8443/dicom-web", "PACS_TOKEN_URL": "https://host.docker.internal:8443/token",
        "PACS_CLIENT_ID": cfg["client_id"], "PACS_CLIENT_SECRET": cfg["client_secret"],
        "PACS_CA_BUNDLE": "/data/pacs-qa/certificate.pem", "PACS_QIDO_PREFIX": "", "PACS_WADO_PREFIX": "", "PACS_SCOPE": "",
        "NEUROCADE_IMAGE": "neurocade:pacs-qa-review",
    }
    retained = [line for line in original.splitlines() if line.partition("=")[0].strip() not in overrides]
    path = STATE / "neurocade-app.env"
    path.write_text("\n".join([*retained, *(f"{key}={value}" for key, value in overrides.items()), ""]))
    path.chmod(0o600)
    print("QA workspace: " + matches[0]["id"])
    print("Launcher overlay: " + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "seed", "serve", "start-gateway", "verify", "export-env", "status", "configure-app"])
    args = parser.parse_args()
    os.umask(0o077)
    {"setup": setup, "seed": seed, "serve": serve, "start-gateway": start_gateway,
     "verify": verify, "export-env": export_env, "status": status, "configure-app": configure_app}[args.action]()


if __name__ == "__main__":
    main()
