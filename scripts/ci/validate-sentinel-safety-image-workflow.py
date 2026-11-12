from __future__ import annotations

import json
import re
import subprocess
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
EXPECTED_METADATA_DIGEST = (
    "sha256:19ffeab6f8bc9293ac2c3fdf94ebe28396254c993aea0b5a542cfb02e0883fa3"
)
DIGEST_FILTER = (
    '.["containerimage.descriptor"].digest '
    '| select(test("^sha256:[0-9a-f]{64}$"))'
)


def require(source: str, marker: str) -> None:
    if marker not in source:
        raise AssertionError(f"missing required marker: {marker}")


def forbid(source: str, marker: str) -> None:
    if marker in source:
        raise AssertionError(f"forbidden marker present: {marker}")


def jq(filter_expression: str, payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", "-er", filter_expression],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def valid_build_run(
    payload: dict[str, object],
    *,
    run_id: int,
    head_branch: str,
    head_sha: str,
    repository: str,
) -> bool:
    repository_payload = payload.get("repository")
    return (
        payload.get("id") == run_id
        and payload.get("run_attempt") == 1
        and payload.get("status") == "completed"
        and payload.get("conclusion") == "success"
        and payload.get("event") == "workflow_dispatch"
        and payload.get("head_branch") == head_branch
        and payload.get("head_sha") == head_sha
        and payload.get("path")
        == ".github/workflows/sentinel-safety-image.yml"
        and isinstance(repository_payload, dict)
        and repository_payload.get("full_name") == repository
    )


def proves_tag_absent(status: int, output: str, image_ref: str) -> bool:
    if status == 0 or image_ref not in output:
        return False
    return re.search(
        r"manifest unknown|name unknown|no such manifest|: not found",
        output.lower(),
    ) is not None


def has_consistent_image_source_sha(source: str) -> bool:
    return (
        "EXPECTED_REVISION" not in source
        and source.count("EXPECTED_IMAGE_SOURCE_SHA") == 3
    )


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
    require(publish, '        type: choice\n        options:\n          - "1"')
    require(publish, "actions: read")
    require(publish, "packages: write")
    require(publish, "actions/runs/$BUILD_RUN_ID")
    require(
        publish,
        "actions/runs/$BUILD_RUN_ID/attempts/1",
    )
    require(publish, '[[ "$BUILD_RUN_ATTEMPT" == "1" ]]')
    require(publish, 'conclusion == "success"')
    require(publish, 'event == "workflow_dispatch"')
    require(publish, 'head_branch == $head_branch')
    require(publish, 'head_sha == $head_sha')
    require(publish, "run_attempt == 1")
    forbid(publish, "attempts/$BUILD_RUN_ATTEMPT")
    forbid(publish, "($attempt | tonumber)")
    require(publish, "EXPECTED_BUILD_HEAD_BRANCH: main")
    require(publish, "EXPECTED_BUILD_HEAD_SHA: ${{ github.workflow_sha }}")
    require(publish, "run-id: ${{ inputs.build_run_id }}")
    require(publish, "expected=(image-amd64 image-arm64)")
    require(publish, "[[ ${#artifacts[@]} -eq 2 ]]")
    require(publish, "for arch in amd64 arm64; do")
    require(publish, 'EXPECTED_VERSION: 4.7.5')
    require(publish, f"EXPECTED_IMAGE_SOURCE_SHA: {SOURCE_REVISION}")
    assert has_consistent_image_source_sha(publish), (
        "EXPECTED_IMAGE_SOURCE_SHA must be defined once and used by both "
        "artifact and published-manifest verification steps"
    )
    missing_source_sha = publish.replace(
        f"      EXPECTED_IMAGE_SOURCE_SHA: {SOURCE_REVISION}\n",
        "",
        1,
    )
    assert not has_consistent_image_source_sha(missing_source_sha)
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
        "digest": EXPECTED_METADATA_DIGEST,
        "size": 4654,
    }
    assert metadata["image.name"] == (
        "ghcr.io/example/coolify-sentinel-safety"
    )

    metadata_result = jq(DIGEST_FILTER, metadata)
    assert metadata_result.returncode == 0, metadata_result.stderr
    assert metadata_result.stdout.strip() == EXPECTED_METADATA_DIGEST

    missing_descriptor = dict(metadata)
    missing_descriptor.pop("containerimage.descriptor")
    assert jq(DIGEST_FILTER, missing_descriptor).returncode != 0

    registry_manifest = {"digest": EXPECTED_METADATA_DIGEST}
    registry_result = jq(".digest", registry_manifest)
    assert registry_result.returncode == 0, registry_result.stderr
    assert registry_result.stdout.strip() == metadata_result.stdout.strip()

    mismatched_manifest = {"digest": f"sha256:{'0' * 64}"}
    mismatch_result = jq(".digest", mismatched_manifest)
    assert mismatch_result.returncode == 0, mismatch_result.stderr
    assert mismatch_result.stdout.strip() != metadata_result.stdout.strip()

    valid_run = {
        "id": 12345,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "workflow-code-sha",
        "path": ".github/workflows/sentinel-safety-image.yml",
        "repository": {"full_name": "svplaksin/coolify"},
    }
    run_args = {
        "run_id": 12345,
        "head_branch": "main",
        "head_sha": "workflow-code-sha",
        "repository": "svplaksin/coolify",
    }
    assert valid_build_run(valid_run, **run_args)
    wrong_attempt_run = {**valid_run, "run_attempt": 2}
    assert not valid_build_run(wrong_attempt_run, **run_args)
    wrong_sha_run = {**valid_run, "head_sha": SOURCE_REVISION}
    assert not valid_build_run(wrong_sha_run, **run_args)

    image_ref = "ghcr.io/svplaksin/coolify-sentinel-safety:test"
    assert proves_tag_absent(1, f"{image_ref}: not found", image_ref)
    assert not proves_tag_absent(1, f"unauthorized: {image_ref}", image_ref)
    assert not proves_tag_absent(1, f"network timeout: {image_ref}", image_ref)
    assert not proves_tag_absent(1, "unrelated: not found", image_ref)
    assert not proves_tag_absent(0, "", image_ref)

    require(runbook, "default branch")
    require(runbook, "separate explicit GO")
    require(runbook, "exactly two OCI artifacts")
    require(runbook, "build run ID")
    require(runbook, "build run attempt")
    require(runbook, "attempt 1")
    require(runbook, "new manual build run")
    require(runbook, "workflow-code SHA")
    require(runbook, "image source SHA")
    forbid(runbook, "|| true")


if __name__ == "__main__":
    main()
