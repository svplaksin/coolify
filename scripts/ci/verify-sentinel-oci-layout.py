#!/usr/bin/env python3
"""Verify a single-platform OCI layout exported by the Coolify Buildx workflow.

Print only the verified runnable image manifest digest for build/publish jobs.
Nested OCI indexes and Buildx provenance attestations are supported; unknown
platform attestations cannot count as runnable images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")


class OciVerificationError(ValueError):
    """The OCI layout does not meet publication safety invariants."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise OciVerificationError(reason)


class Inspector:
    def __init__(self, root: Path, arch: str, revision: str, version: str) -> None:
        self.root = root
        self.arch = arch
        self.revision = revision
        self.version = version
        self.verified_blobs: set[str] = set()
        self.runnable: list[str] = []
        self.visited_indexes: set[str] = set()

    def blob(self, descriptor: dict, *, json_payload: bool = False):
        require(isinstance(descriptor, dict), "invalid OCI descriptor")
        digest = descriptor.get("digest")
        require(isinstance(digest, str), "missing digest")
        match = DIGEST_PATTERN.fullmatch(digest)
        require(match is not None, "invalid blob digest: " + digest)
        expected_size = descriptor.get("size")
        require(isinstance(expected_size, int) and not isinstance(expected_size, bool)
                and expected_size >= 0, "invalid blob size")
        path = self.root / "blobs" / "sha256" / match.group(1)
        require(path.is_file() and not path.is_symlink(), f"missing/unsafe blob {digest}")
        require(path.stat().st_size == expected_size, f"blob size mismatch: {digest}")
        if digest not in self.verified_blobs:
            h = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    h.update(block)
            require(h.hexdigest() == match.group(1), f"blob digest mismatch: {digest}")
            self.verified_blobs.add(digest)
        if json_payload:
            require(expected_size <= 8 * 1024 * 1024, "oversize OCI metadata")
            try:
                return json.loads(path.read_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OciVerificationError(f"invalid OCI JSON {digest}") from exc
        return None

    def metadata(self, descriptor: dict, expected_media_type: str) -> dict:
        require(descriptor.get("mediaType") == expected_media_type,
                f"unexpected OCI descriptor media type: {descriptor.get('mediaType')}")
        obj = self.blob(descriptor, json_payload=True)
        require(isinstance(obj, dict) and obj.get("schemaVersion") == 2
                and obj.get("mediaType") == expected_media_type,
                "OCI content type/schema mismatch")
        return obj

    def verify_manifest(self, desc: dict, *, runnable: bool) -> None:
        manifest = self.metadata(desc, OCI_MANIFEST)
        config_desc = manifest.get("config")
        require(isinstance(config_desc, dict), "image manifest missing config")
        if runnable:
            require(config_desc.get("mediaType") == OCI_CONFIG,
                    "runnable image has wrong config media type")
        config = self.blob(config_desc, json_payload=True)
        require(isinstance(config, dict), "invalid image config")
        layers = manifest.get("layers")
        require(isinstance(layers, list), "invalid OCI image layers")
        for layer in layers:
            self.blob(layer)
        if runnable:
            require(config.get("os") == "linux" and config.get("architecture") == self.arch,
                    "config platform differs from image descriptor")
            labels = config.get("config", {}).get("Labels", {})
            require(isinstance(labels, dict), "missing OCI labels")
            require(labels.get("org.opencontainers.image.revision") == self.revision,
                    "unexpected source revision label")
            require(labels.get("org.opencontainers.image.version") == self.version,
                    "unexpected Coolify version label")
            self.runnable.append(desc["digest"])

    def visit_index(self, desc: dict, depth: int) -> None:
        require(depth <= 6, "OCI index recursion depth exceeded")
        digest = desc["digest"]
        require(digest not in self.visited_indexes, "repeated/cyclic OCI index")
        self.visited_indexes.add(digest)
        contents = self.metadata(desc, OCI_INDEX)
        children = contents.get("manifests")
        require(isinstance(children, list) and bool(children), "empty OCI index")
        for child in children:
            require(isinstance(child, dict), "invalid child OCI descriptor")
            media_type = child.get("mediaType")
            platform = child.get("platform")
            if media_type == OCI_INDEX:
                require(platform is None, "nested OCI index must not carry a platform")
                self.visit_index(child, depth + 1)
                continue
            require(media_type == OCI_MANIFEST, "unknown OCI content media type")
            require(isinstance(platform, dict), "missing OCI image platform")
            os_name, arch = platform.get("os"), platform.get("architecture")
            if os_name == "unknown" and arch == "unknown":
                annotations = child.get("annotations")
                require(isinstance(annotations, dict) and
                        annotations.get("vnd.docker.reference.type") == "attestation-manifest",
                        "unrecognized unknown/unknown manifest")
                self.verify_manifest(child, runnable=False)
            else:
                require(os_name == "linux" and arch == self.arch,
                        f"unexpected runnable platform: {os_name}/{arch}")
                self.verify_manifest(child, runnable=True)

    def inspect(self) -> str:
        require(self.root.is_dir(), "OCI layout directory not found")
        try:
            layout = json.loads((self.root / "oci-layout").read_text())
            index = json.loads((self.root / "index.json").read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OciVerificationError("missing/invalid OCI layout metadata") from exc
        require(layout == {"imageLayoutVersion": "1.0.0"}, "unsupported OCI layout version")
        require(isinstance(index, dict) and index.get("schemaVersion") == 2
                and index.get("mediaType") == OCI_INDEX,
                "invalid root OCI index")
        refs = index.get("manifests")
        require(isinstance(refs, list) and len(refs) == 1,
                "expected exactly one root OCI index descriptor")
        require(refs[0].get("mediaType") == OCI_INDEX,
                "expected nested OCI index in exported layout")
        self.visit_index(refs[0], 0)
        require(len(self.runnable) == 1,
                f"expected exactly one {self.arch} image; found {len(self.runnable)}")
        return self.runnable[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        print(Inspector(args.layout, args.arch, args.revision, args.version).inspect())
        return 0
    except (OciVerificationError, OSError, TypeError, ValueError, KeyError) as exc:
        print(f"OCI verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
