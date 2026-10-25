#!/usr/bin/env bash
# docker-verify.sh — R-DKR-01 (docs/specs/recorder-freeze.md): build the locked clean-room
# image and run the test suite inside it with no network. Exits non-zero on any failure.
#
#   ./scripts/docker-verify.sh            build + run
#   ETHWALL_DOCKER_IMAGE=name ./scripts/docker-verify.sh
#
# Inside the container the `e2e` marker is deselected: those tests drive Docker from the host
# (this script itself), so running them in the container would be Docker-in-Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${ETHWALL_DOCKER_IMAGE:-ethwall-cleanroom:local}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker-verify: docker not found on PATH" >&2
  exit 1
fi

echo "docker-verify: building $IMAGE"
docker build -t "$IMAGE" "$ROOT"

echo "docker-verify: pytest in container (--network none)"
MSYS_NO_PATHCONV=1 docker run --rm --network none "$IMAGE" \
  uv run --offline --frozen pytest -q -p no:cacheprovider -m "not e2e"

# TODO(integrator): once `ethwall run` exists (docs/specs/cli-integration.md R-CLI-01), also run
# one mock episode per condition here, e.g.
#   MSYS_NO_PATHCONV=1 docker run --rm --network none "$IMAGE" \
#     uv run --offline --frozen ethwall run --condition baseline,guarded --seeds 100-100 \
#       --mode mock --policy honest --partition mock-docker
# followed by `ethwall verify` on the produced runs, so R-DKR-01's "mock episode in both
# conditions" is covered. Keep `set -e` so any failure exits non-zero.

echo "docker-verify: OK"
