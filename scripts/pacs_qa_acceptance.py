"""Opt-in, local-only PACS conversion/outage acceptance checks.

Run from the repository root: python -m scripts.pacs_qa_acceptance --help.
The default is read-only preflight. --execute creates six new research copies.
--outage additionally stops/restarts ONLY the labelled dummy Orthanc archive.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from uuid import uuid4

import requests

from scripts import pacs_qa

BASE = "http://localhost:8000/api/app"
ACTIVE = {"queued", "running", "canceling"}
TERMINAL = {"completed", "completed_with_errors", "failed", "interrupted", "canceled"}

# Read-only inspection runs in the application container to use its actual
# database and manifest-backed storage paths, not guessed host directory names.
INSPECT = """
import json, sys
from api_service.runtime import settings
from backend_common.db import SessionLocal, BackgroundJob, PacsImport
from backend_common.case_storage import case_storage_dir
workspace, identifiers = json.loads(sys.argv[1])
with SessionLocal() as db:
    active = db.query(BackgroundJob).filter(BackgroundJob.state.in_(['queued', 'running'])).count()
    checks = []
    for identifier in identifiers:
        row = db.get(PacsImport, identifier)
        if row is None or row.workspace_id != workspace:
            raise RuntimeError('Import does not belong to the QA workspace')
        stage = settings.fs_data_root / '.tmp' / 'pacs-imports' / identifier
        case_dir = case_storage_dir(settings, workspace, row.case_id)
        files = [p for directory in case_dir.glob('pacs-series-*') for p in directory.rglob('*') if p.is_file()]
        allowed = ('.nii.gz', '.nii', '.json', '.bval', '.bvec')
        checks.append({'import_id': identifier, 'staging_removed': not stage.exists(),
                       'published_files_allowed': all(not p.is_symlink() and p.name.endswith(allowed) for p in files),
                       'has_volume': any(p.name.endswith(('.nii.gz', '.nii')) for p in files)})
print(json.dumps({'active_jobs': active, 'cleanup': checks}))
"""


def api(method, path, **kwargs):
    response = requests.request(method, BASE + path, timeout=(5, 120), allow_redirects=False, **kwargs)
    response.raise_for_status()
    return response.json()


def inspect_app(workspace, identifiers):
    result = pacs_qa.docker("exec", "neurocade", "python", "-c", INSPECT,
                            json.dumps([workspace, identifiers]), capture=True)
    return json.loads(result.stdout)


def preflight(workspace):
    matches = [row for row in api("GET", "/workspaces") if row["id"] == workspace and row["name"] == "pacs-qa"]
    if len(matches) != 1:
        raise RuntimeError("Select the exact dedicated pacs-qa workspace; no other workspace is allowed")
    status = api("GET", "/pacs/status", params={"workspace_id": workspace})
    if not status.get("enabled") or status.get("source") != "local-remind-qa":
        raise RuntimeError("Application is not connected to the local QA fixture")
    info = json.loads(pacs_qa.docker("inspect", "--format", "{{json .}}", pacs_qa.CONTAINER, capture=True).stdout)
    if (info["Config"].get("Labels") or {}).get("org.neurocade.qa") != str(pacs_qa.STATE) or not info["State"]["Running"]:
        raise RuntimeError("Dummy PACS ownership/running-state check failed")
    if inspect_app(workspace, [])["active_jobs"]:
        raise RuntimeError("Wait for active application jobs before acceptance QA")
    if any(row["state"] in ACTIVE for row in api("GET", "/pacs/imports", params={"workspace_id": workspace})):
        raise RuntimeError("Wait for active PACS imports before acceptance QA")
    inventory = json.loads((pacs_qa.STATE / "inventory.json").read_text())
    if len(inventory) != 6 or {row["patient_id"] for row in inventory} != {f"ReMIND-{n:03}" for n in range(1, 7)}:
        raise RuntimeError("Expected the six recorded public ReMIND sample series")
    return inventory


def wait_import(identifier, timeout=300):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        row = api("GET", f"/pacs/imports/{identifier}")
        if row["state"] != previous:
            print(f'{identifier}: {row["state"]}', flush=True)
            previous = row["state"]
        if row.get("error_code") == "cleanup_failed":
            raise RuntimeError("Raw-image cleanup failed; operator attention required")
        if row["state"] in TERMINAL:
            return row
        time.sleep(1)
    raise TimeoutError(f"Import {identifier} is still active; inspect it before another QA run")


def wait_archive(timeout=30):
    cfg = pacs_qa.configuration()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(pacs_qa.ORTHANC + "/system", auth=(cfg["username"], cfg["password"]), timeout=2)
            if response.ok:
                return
        except requests.RequestException:
            # The archive is expected to refuse connections during restart.
            pass
        time.sleep(1)
    raise TimeoutError("Dummy PACS did not become healthy after restart")


def outage_and_retry(row, workspace):
    try:
        pacs_qa.docker("stop", "-t", "0", pacs_qa.CONTAINER)
        failed = wait_import(row["id"])
        errors = {item.get("error_code") for item in failed["series"]}
        if failed["state"] != "failed" or not errors.intersection({"connection_failed", "retrieval_failed"}):
            raise RuntimeError("Outage did not produce the expected retrieval failure; do not count it as a pass")
        cleanup = inspect_app(workspace, [row["id"]])["cleanup"]
        if not all(item["staging_removed"] for item in cleanup):
            raise RuntimeError("Failed import retained raw staging")
    finally:
        # Restore even if the assertion, request, or user interrupt fails.
        pacs_qa.docker("start", pacs_qa.CONTAINER)
        wait_archive()
    return api("POST", f'/pacs/imports/{row["id"]}/retry')


def run(workspace, *, execute=False, outage=False):
    inventory = preflight(workspace)
    if not execute:
        print("Preflight passed. --execute creates six QA copies; --outage also interrupts the dummy PACS.")
        return
    report = {"workspace_id": workspace, "outage_requested": outage, "status": "running", "imports": []}
    report_path = pacs_qa.STATE / f"acceptance-{uuid4()}.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    try:
        for index, sample in enumerate(inventory):
            studies = api("POST", "/pacs/studies/search", json={"workspace_id": workspace, "patient_id": sample["patient_id"]})
            if not any(row["study_uid"] == sample["study_uid"] for row in studies):
                raise RuntimeError("Recorded study not found")
            series = api("GET", f'/pacs/studies/{sample["study_uid"]}/series', params={"workspace_id": workspace})
            if not any(row["series_uid"] == sample["series_uid"] and row["eligible"] for row in series):
                raise RuntimeError("Recorded series not eligible")
            row = api("POST", "/pacs/imports", json={"workspace_id": workspace, "study_uid": sample["study_uid"],
                "series_uids": [sample["series_uid"]], "submission_key": str(uuid4()),
                "confirm_duplicate": True, "verified_t1_series_uids": []})
            record = {"patient_id": sample["patient_id"], "import_id": row["id"], "case_id": row["case_id"], "state": row["state"]}
            report["imports"].append(record)
            save()
            if outage and index == 0:
                row = outage_and_retry(row, workspace)
                record["outage_retried"] = True
            row = wait_import(row["id"])
            record["state"] = row["state"]
            if row["state"] != "completed" or sum(s.get("retrieved_count", 0) for s in row["series"]) != sample["instances"]:
                raise RuntimeError("Conversion or complete retrieval did not pass")
            checks = inspect_app(workspace, [row["id"]])["cleanup"]
            record["cleanup"] = checks
            if not checks or not all(item["staging_removed"] and item["published_files_allowed"] and item["has_volume"] for item in checks):
                raise RuntimeError("Publication/cleanup verification failed")
            save()
        report["status"] = "passed"
    except BaseException:
        report["status"] = "failed_or_interrupted"
        raise
    finally:
        save()
        print("QA report: " + str(report_path), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--outage", action="store_true")
    args = parser.parse_args()
    if args.outage and not args.execute:
        parser.error("--outage requires --execute")
    os.umask(0o077)
    run(args.workspace_id, execute=args.execute, outage=args.outage)


if __name__ == "__main__":
    main()
