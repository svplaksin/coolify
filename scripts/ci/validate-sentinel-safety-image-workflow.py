from __future__ import annotations

import json
from pathlib import Path


BUILD_WORKFLOW = Path(".github/workflows/sentinel-safety-image.yml")
PUBLISH_WORKFLOW = Path(
    ".github/workflows/sentinel-safety-image-publish.yml"
)
ACTIONLINT_CONFIG = Path(".github/actionlint.yaml")
IMAGETOOLS_METADATA_FIXTURE = Path(
    "scripts/ci/fixtures/imagetools-create-metadata.json"
)
RUNBOOK = Path(
    "docs/operations/sentinel-shared-host-ownership-v4.7.5.md"
)
SOURCE_REVISION = "bd04cd1f34cc854ee44be3715f7c0afb3e0a8b7a"


def require(source: str, marker: str) -> None:
    if marker not in source:
        raise AssertionError(f"missing required marker: {marker}")


def forbid(source: str, marker: str) -> None:
    if marker in source:
        raise AssertionError(f"forbidden marker present: {marker}")


def main() -> None:
    build = BUILD_WORKFLOW.read_text(encoding="utf-8")
    publish = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    actionlint_config = ACTIONLINT_CONFIG.read_text(encoding="utf-8")
    metadata = json.loads(
        IMAGETOOLS_METADATA_FIXTURE.read_text(encoding="utf-8")
    )
    runbook = RUNBOOK.read_text(encoding="utf-8")

    require(build, "  workflow_dispatch:")
    forbid(build, "  push:")
    require(build, "uses: ./.github/workflows/build.yaml")
    require(build, f"ref: {SOURCE_REVISION}")
    require(build, "coolify: 4.7.5")
    require(build, "permissions: {}")
    require(build, "contents: read")
    forbid(build, "packages: write")
    forbid(build, "docker/login-action")
    forbid(build, "imagetools create")

    require(build, "expected=(image-amd64 image-arm64)")
    require(build, "[[ ${#artifacts[@]} -eq 2 ]]")
    require(build, "for arch in amd64 arm64; do")
    require(build, '.platform.os == "linux"')
    require(build, ".platform.architecture == $arch")
    require(build, 'EXPECTED_VERSION: 4.7.5')
    require(build, f"EXPECTED_IMAGE_SOURCE_SHA: {SOURCE_REVISION}")

    require(publish, "  workflow_dispatch:")
    forbid(publish, "  push:")
    require(publish, "build_run_id:")
    require(publish, "build_run_attempt:")
    require(publish, "actions: read")
    require(publish, "packages: write")
    require(publish, "actions/runs/$BUILD_RUN_ID")
    require(
        publish,
        "actions/runs/$BUILD_RUN_ID/attempts/$BUILD_RUN_ATTEMPT",
    )
    require(publish, 'conclusion == "success"')
    require(publish, 'event == "workflow_dispatch"')
    require(publish, 'head_branch == $head_branch')
    require(publish, 'head_sha == $head_sha')
    require(publish, 'run_attempt == ($attempt | tonumber)')
    require(publish, "EXPECTED_BUILD_HEAD_BRANCH: main")
    require(publish, "EXPECTED_BUILD_HEAD_SHA: ${{ github.workflow_sha }}")
    require(publish, "run-id: ${{ inputs.build_run_id }}")
    require(publish, "expected=(image-amd64 image-arm64)")
    require(publish, "[[ ${#artifacts[@]} -eq 2 ]]")
    require(publish, "for arch in amd64 arm64; do")
    require(publish, 'EXPECTED_VERSION: 4.7.5')
    require(publish, f"EXPECTED_IMAGE_SOURCE_SHA: {SOURCE_REVISION}")
    require(
        publish,
        "ghcr.io/${{ github.repository_owner }}/coolify-sentinel-safety",
    )
    require(publish, "${{ inputs.build_run_id }}")
    require(publish, "${{ github.run_id }}")
    require(publish, "${{ github.run_attempt }}")
    require(publish, "Refuse to overwrite an existing tag")
    require(publish, "set +e")
    require(publish, "probe_status=$?")
    require(publish, 'grep -Fq "$IMAGE:$TAG"')
    require(publish, "manifest unknown")
    require(publish, "name unknown")
    require(publish, "no such manifest")
    require(publish, "Unable to prove that the tag is unused")
    require(publish, '.["containerimage.descriptor"].digest')
    forbid(publish, '.["containerimage.digest"]')
    require(publish, "--format '{{json .Manifest}}'")
    require(publish, "registry_digest=\"$(jq -er '.digest'")
    require(publish, '[[ "$registry_digest" != "$digest" ]]')
    require(publish, 'sort == ["linux/amd64", "linux/arm64"]')
    require(publish, "org.opencontainers.image.revision")
    require(publish, "org.opencontainers.image.version")

    for workflow in (build, publish):
        forbid(workflow, ":latest")
        forbid(workflow, "appleboy/ssh")
        forbid(workflow, "ssh-action")
        forbid(workflow, "docker context")
        forbid(workflow, "api/v1/deploy")

    require(actionlint_config, "self-hosted-runner:")
    require(actionlint_config, "- ubuntu-26.04")

    descriptor = metadata["containerimage.descriptor"]
    assert descriptor == {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "digest": (
            "sha256:19ffeab6f8bc9293ac2c3fdf94ebe28396254c993aea0b5a542cfb02e0883fa3"
        ),
        "size": 4654,
    }
    assert metadata["image.name"] == (
        "ghcr.io/example/coolify-sentinel-safety"
    )

    require(runbook, "default branch")
    require(runbook, "separate explicit GO")
    require(runbook, "exactly two OCI artifacts")
    require(runbook, "build run ID")
    require(runbook, "build run attempt")
    require(runbook, "workflow-code SHA")
    require(runbook, "image source SHA")
    forbid(runbook, "|| true")


if __name__ == "__main__":
    main()
