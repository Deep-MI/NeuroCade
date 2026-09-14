"""Rebuild the pinned, offline documentation snapshot from local Git checkouts."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

FASTSURFER_REVISION = "7e5334356e5abc0b600c11d6e9890e386d176433"
NEUROCADE_REVISION = "3f28b26bfd8a4a98663d9a0f9cbb906db147bfcf"


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastsurfer-checkout", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "config/documentation"
    pages = []
    paths = git(args.fastsurfer_checkout, "ls-tree", "-r", "--name-only", FASTSURFER_REVISION, "doc/overview").splitlines()
    paths = sorted(path for path in paths if path.endswith(".md") and path.count("/") == 2) + ["README.md"]
    for product, version, checkout, files, revision, repository in [
        ("fastsurfer", "2.4.2", args.fastsurfer_checkout, paths, FASTSURFER_REVISION, "FastSurfer"),
        ("neurocade", "3f28b26", root, ["README.md", "INSTALL.md"], NEUROCADE_REVISION, "NeuroCade"),
    ]:
        for path in files:
            source = git(checkout, "show", f"{revision}:{path}")
            for index, chunk in enumerate(re.split(r"(?m)(?=^#{1,3} )", source)):
                if chunk.strip():
                    pages.append(
                        {
                            "product": product,
                            "version": version,
                            "page_id": f"{product}/{version}/{path}/{index}",
                            "heading": chunk.splitlines()[0].lstrip("# "),
                            "source_revision": revision,
                            "url": f"https://github.com/Deep-MI/{repository}/blob/{revision}/{path}",
                            "text": chunk,
                            "digest": hashlib.sha256(chunk.encode()).hexdigest(),
                        }
                    )
    data = {
        "versions": {"fastsurfer": ["2.4.2"], "neurocade": ["3f28b26"]},
        "image_versions": {"vnmd/fastsurfer_2.4.2:20260115": "2.4.2"},
        "pages": pages,
    }
    raw = (json.dumps(data, indent=2) + "\n").encode()
    (output / "bundle.json").write_bytes(raw)
    (output / "bundle.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    (output / "FastSurfer-LICENSE").write_text(git(args.fastsurfer_checkout, "show", f"{FASTSURFER_REVISION}:LICENSE"))
    print(f"Wrote {len(pages)} documentation sections")


if __name__ == "__main__":
    main()
