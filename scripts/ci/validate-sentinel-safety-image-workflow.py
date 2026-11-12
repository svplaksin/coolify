from __future__ import annotations

from pathlib import Path


BUILD_WORKFLOW = Path(".github/workflows/sentinel-safety-image.yml")
PUBLISH_WORKFLOW = Path(
    ".github/workflows/sentinel-safety-image-publish.yml"
)
ACTIONLINT_CONFIG = Path(".github/actionlint.yaml")
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
    require(build, f"EXPECTED_REVISION: {SOURCE_REVISION}")

    require(publish, "  workflow_dispatch:")
    forbid(publish, "  push:")
    require(publish, "build_run_id:")
    require(publish, "actions: read")
    require(publish, "packages: write")
    require(publish, "actions/runs/$BUILD_RUN_ID")
    require(publish, 'conclusion == "success"')
    require(publish, 'event == "workflow_dispatch"')
    require(publish, "run-id: ${{ inputs.build_run_id }}")
    require(publish, "expected=(image-amd64 image-arm64)")
    require(publish, "[[ ${#artifacts[@]} -eq 2 ]]")
    require(publish, "for arch in amd64 arm64; do")
    require(publish, 'EXPECTED_VERSION: 4.7.5')
    require(publish, f"EXPECTED_REVISION: {SOURCE_REVISION}")
    require(
        publish,
        "ghcr.io/${{ github.repository_owner }}/coolify-sentinel-safety",
    )
    require(publish, "${{ inputs.build_run_id }}")
    require(publish, "${{ github.run_id }}")
    require(publish, "${{ github.run_attempt }}")
    require(publish, "Refuse to overwrite an existing tag")
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

    require(runbook, "default branch")
    require(runbook, "separate explicit GO")
    require(runbook, "exactly two OCI artifacts")
    require(runbook, "build run ID")
    forbid(runbook, "|| true")


if __name__ == "__main__":
    main()
