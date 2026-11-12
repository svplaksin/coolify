"""Tests for the nested OCI layout produced by the reviewed Coolify build.

The synthetic test layout reproduces run 43183203133: a top-level index
references a nested OCI index containing one platform image and one
unknown/unknown attestation manifest.
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "verify-sentinel-oci-layout.py"
REVISION = "bd04cd1f34cc854ee44be3715f7c0afb3e0a8b7a"
VERSION = "4.7.5"
INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"


class LayoutFixture:
    def __init__(self, root, arch="amd64"):
        self.root = root
        self.arch = arch
        (root / "blobs/sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        self.image_descriptor = None
        self.attestation_descriptor = None
        self.nested_descriptor = None
        self.construct()

    def blob(self, payload):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        (self.root / "blobs/sha256" / digest).write_bytes(payload)
        return {"digest": "sha256:" + digest, "size": len(payload)}

    def construct(self, *, labels=None):
        if labels is None:
            labels = {
                "org.opencontainers.image.revision": REVISION,
                "org.opencontainers.image.version": VERSION,
            }
        conf = self.blob({"os": "linux", "architecture": self.arch, "config": {"Labels": labels}})
        image = self.blob({"schemaVersion": 2, "mediaType": MANIFEST,
                           "config": {"mediaType": CONFIG, **conf}, "layers": [
                               {"mediaType": "application/vnd.oci.image.layer.v1.tar", **self.blob(b"layer")}]})
        self.image_descriptor = {"mediaType": MANIFEST, **image,
                                 "platform": {"os": "linux", "architecture": self.arch}}
        att_conf = self.blob({})
        att = self.blob({"schemaVersion": 2, "mediaType": MANIFEST,
                         "config": {"mediaType": "application/vnd.oci.empty.v1+json", **att_conf},
                         "layers": [{"mediaType": "application/vnd.in-toto+json", **self.blob(b"attest")}]})
        self.attestation_descriptor = {"mediaType": MANIFEST, **att,
                                       "annotations": {"vnd.docker.reference.type": "attestation-manifest",
                                                       "vnd.docker.reference.digest": image["digest"]},
                                       "platform": {"os": "unknown", "architecture": "unknown"}}
        nested = self.blob({"schemaVersion": 2, "mediaType": INDEX,
                            "manifests": [self.image_descriptor, self.attestation_descriptor]})
        self.nested_descriptor = {"mediaType": INDEX, **nested}
        self.reindex()

    def reindex(self, descriptors=None):
        descriptors = [self.nested_descriptor] if descriptors is None else descriptors
        (self.root / "index.json").write_text(json.dumps({"schemaVersion": 2, "mediaType": INDEX,
                                                            "manifests": descriptors}))

    def replace_nested(self, descriptors):
        nested = self.blob({"schemaVersion": 2, "mediaType": INDEX, "manifests": descriptors})
        self.nested_descriptor = {"mediaType": INDEX, **nested}
        self.reindex()


class OciLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.layout = LayoutFixture(Path(self.tmp.name))

    def verify(self, arch="amd64"):
        return subprocess.run([sys.executable, str(SCRIPT), "--layout", self.tmp.name,
                               "--arch", arch, "--revision", REVISION, "--version", VERSION],
                              capture_output=True, text=True)

    def assertRejected(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OCI verification failed:", result.stderr)

    def test_nested_index_with_attestation_passes(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.layout.image_descriptor["digest"])

    def test_wrong_architecture_rejected(self):
        self.assertRejected(self.verify("arm64"))

    def test_duplicate_runnable_image_rejected(self):
        self.layout.replace_nested([self.layout.image_descriptor, self.layout.image_descriptor,
                                    self.layout.attestation_descriptor])
        self.assertRejected(self.verify())

    def test_unexpected_other_runnable_platform_rejected(self):
        unwanted = {**self.layout.image_descriptor,
                    "platform": {"os": "linux", "architecture": "arm64"}}
        self.layout.replace_nested([self.layout.image_descriptor, unwanted,
                                    self.layout.attestation_descriptor])
        self.assertRejected(self.verify())

    def test_malformed_attestation_rejected(self):
        bad = {**self.layout.attestation_descriptor, "annotations": {}}
        self.layout.replace_nested([self.layout.image_descriptor, bad])
        self.assertRejected(self.verify())

    def test_attestation_reference_must_target_verified_image(self):
        attestation = {
            **self.layout.attestation_descriptor,
            "annotations": {
                **self.layout.attestation_descriptor["annotations"],
                "vnd.docker.reference.digest": "sha256:" + "0" * 64,
            },
        }
        self.layout.replace_nested([self.layout.image_descriptor, attestation])
        self.assertRejected(self.verify())

    def test_attestation_reference_cannot_be_missing(self):
        attestation = {
            **self.layout.attestation_descriptor,
            "annotations": {
                "vnd.docker.reference.type": "attestation-manifest",
            },
        }
        self.layout.replace_nested([self.layout.image_descriptor, attestation])
        self.assertRejected(self.verify())

    def test_missing_blob_rejected(self):
        (self.layout.root / "blobs/sha256" /
         self.layout.image_descriptor["digest"].split(":")[1]).unlink()
        self.assertRejected(self.verify())

    def test_tampered_blob_rejected(self):
        blob = self.layout.root / "blobs/sha256" / self.layout.image_descriptor["digest"].split(":")[1]
        blob.write_bytes(blob.read_bytes() + b"evil")
        self.assertRejected(self.verify())

    def test_incorrect_oci_labels_rejected(self):
        self.layout.construct(labels={"org.opencontainers.image.revision": "wrong",
                                      "org.opencontainers.image.version": VERSION})
        self.assertRejected(self.verify())

    def test_mismatched_config_platform_rejected(self):
        self.layout.construct()
        image = self.layout.image_descriptor
        p = self.layout.root / "blobs/sha256" / image["digest"].split(":")[1]
        parsed = json.loads(p.read_bytes())
        conf_path = self.layout.root / "blobs/sha256" / parsed["config"]["digest"].split(":")[1]
        conf = json.loads(conf_path.read_bytes())
        conf["architecture"] = "arm64"
        # Updating the blob and its descriptor keeps digests correct, so only the platform check can reject.
        new_conf = self.layout.blob(conf)
        parsed["config"] = {"mediaType": CONFIG, **new_conf}
        new_manifest = self.layout.blob(parsed)
        self.layout.image_descriptor = {"mediaType": MANIFEST, **new_manifest,
                                        "platform": {"os": "linux", "architecture": "amd64"}}
        self.layout.replace_nested([self.layout.image_descriptor])
        self.assertRejected(self.verify())

    def test_tampered_layer_rejected(self):
        layer = self.layout.root / "blobs/sha256" / hashlib.sha256(b"layer").hexdigest()
        layer.write_bytes(b"tampered")
        self.assertRejected(self.verify())


if __name__ == "__main__":
    unittest.main()
