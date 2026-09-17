#!/usr/bin/env python3
"""Resume release publication without rebuilding or replacing published releases."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def releases() -> list[dict[str, Any]]:
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{os.environ['GITHUB_REPOSITORY']}/releases"))
    return [release for page in pages for release in page]


def read_plan(path: Path) -> dict[str, str]:
    plan = dict(line.split("=", 1) for line in path.read_text().splitlines())
    if not re.fullmatch(r"v\d{4}\.\d{1,2}\.\d{1,2}(?:-beta\.\d+|-\d+)?", plan["tag"]):
        raise ValueError("Invalid release tag")
    if not re.fullmatch(r"[0-9a-f]{40}", plan["head_sha"]):
        raise ValueError("Invalid release commit")
    if plan["version"] != plan["tag"][1:] or plan["kind"] not in {"beta", "stable"}:
        raise ValueError("Invalid release plan")
    if plan["prerelease"] != str(plan["kind"] == "beta").lower():
        raise ValueError("Release kind mismatch")
    if ("-beta." in plan["tag"]) != (plan["kind"] == "beta"):
        raise ValueError("Release tag kind mismatch")
    if plan["should_release"] not in {"true", "false"}:
        raise ValueError("Invalid release decision")
    return plan


def check_tag(plan: dict[str, str]) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{plan['tag']}^{{commit}}"],
        capture_output=True, text=True,
    )
    if result.returncode:
        return False
    if result.stdout.strip() != plan["head_sha"]:
        raise ValueError("Release tag already belongs to a different commit")
    return True


def existing_release(plan: dict[str, str]) -> dict[str, Any] | None:
    release = next((item for item in releases() if item["tag_name"] == plan["tag"]), None)
    if release and not check_tag(plan) and release["target_commitish"] != plan["head_sha"]:
        raise ValueError("Release draft belongs to a different commit")
    if release and release["prerelease"] != (plan["kind"] == "beta"):
        raise ValueError("Existing release has a different release kind")
    return release


def prepare(path: Path) -> None:
    plan = read_plan(path)
    check_tag(plan)
    release = existing_release(plan) if plan["should_release"] == "true" else None
    published = bool(release and not release["draft"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key in ("tag", "version", "kind", "prerelease", "head_sha", "should_release", "latest_reachable_release_tag"):
            output.write(f"{key}={plan[key]}\n")
        output.write(f"published={str(published).lower()}\n")
    # A retry must build the original source, even if the branch moved meanwhile.
    run("git", "checkout", "--detach", plan["head_sha"])


def newer_release_exists(plan: dict[str, str]) -> bool:
    pattern = r"v(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-beta\.(\d+))?" if plan["kind"] == "beta" else r"v(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d+))?"

    def version(tag: str) -> tuple[int, ...]:
        match = re.fullmatch(pattern, tag)
        return tuple(int(part or 0) for part in match.groups()) if match else ()

    return any(
        not item["draft"] and item["prerelease"] == (plan["kind"] == "beta")
        and version(item["tag_name"]) > version(plan["tag"])
        for item in releases()
    )


def publish(path: Path) -> None:
    plan = read_plan(path)
    tag = plan["tag"]
    has_tag = check_tag(plan)
    release = existing_release(plan)
    image = f"docker.io/deepmi/neurocade:{tag}"
    if not release or release["draft"]:
        if not release:
            run("gh", "release", "create", tag, "--draft", "--target", plan["head_sha"],
                "--title", f"NeuroCade {plan['version']}", "--generate-notes",
                f"--prerelease={plan['prerelease']}")
        # Incomplete assets remain hidden in a draft until every upload succeeds.
        sif, wheel, source = os.environ["APP_SIF"], os.environ["BRIDGE_WHEEL"], os.environ["SOURCE_ARCHIVE"]
        run("gh", "release", "upload", tag, sif, f"{sif}.sha256", wheel, f"{wheel}.sha256",
            source, f"{source}.sha256", "neurocade-release.json", "--clobber")
        run("docker", "buildx", "imagetools", "create", "--tag", image, os.environ["CANDIDATE_DIGEST_IMAGE"])
        if not has_tag:
            run("git", "config", "user.name", "github-actions[bot]")
            run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
            run("git", "tag", "-a", tag, plan["head_sha"], "-m", f"NeuroCade {tag}")
            run("git", "push", "origin", f"refs/tags/{tag}")
        run("gh", "release", "edit", tag, "--draft=false", "--verify-tag",
            f"--latest={str(plan['kind'] == 'stable' and not newer_release_exists(plan)).lower()}")
    # A retry after publication only repairs the channel; published assets stay intact.
    current = existing_release(plan)
    if not current or current["draft"]:
        raise ValueError("Release publication was not confirmed")
    if newer_release_exists(plan):
        print("A newer release exists; leaving its channel tag unchanged.")
        return
    channel = "beta" if plan["kind"] == "beta" else "latest"
    run("docker", "buildx", "imagetools", "create", "--tag", f"docker.io/deepmi/neurocade:{channel}", image)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "publish"])
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    {"prepare": prepare, "publish": publish}[args.command](args.plan)
