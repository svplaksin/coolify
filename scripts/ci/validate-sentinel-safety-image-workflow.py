from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/sentinel-safety-image.yml")
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
    workflow = WORKFLOW.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    require(workflow, "  workflow_dispatch:")
    forbid(workflow, "  push:")
    require(workflow, "uses: ./.github/workflows/build.yaml")
    require(workflow, f"ref: {SOURCE_REVISION}")
    require(workflow, "coolify: 4.7.5")
    require(workflow, "permissions: {}")
    require(workflow, "contents: read")
    require(workflow, "packages: write")

    require(workflow, "expected=(image-amd64 image-arm64)")
    require(workflow, "[[ ${#artifacts[@]} -eq 2 ]]")
    require(workflow, "for arch in amd64 arm64; do")
    require(workflow, '.platform.os == "linux"')
    require(workflow, ".platform.architecture == $arch")
    require(workflow, 'EXPECTED_VERSION: 4.7.5')
    require(workflow, f"EXPECTED_REVISION: {SOURCE_REVISION}")
    require(
        workflow,
        "ghcr.io/${{ github.repository_owner }}/coolify-sentinel-safety",
    )
    require(workflow, f"TAG: 4.7.5-{SOURCE_REVISION}")
    require(workflow, 'sort == ["linux/amd64", "linux/arm64"]')
    require(workflow, "org.opencontainers.image.revision")
    require(workflow, "org.opencontainers.image.version")

    forbid(workflow, ":latest")
    forbid(workflow, "appleboy/ssh")
    forbid(workflow, "ssh-action")
    forbid(workflow, "docker context")
    forbid(workflow, "api/v1/deploy")

    require(runbook, "default branch")
    require(runbook, "separate explicit GO")
    require(runbook, "exactly two OCI artifacts")
    forbid(runbook, "|| true")


if __name__ == "__main__":
    main()
