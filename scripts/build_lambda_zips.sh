#!/usr/bin/env bash
# zip root must match the handler's dotted import path (services.trigger.handler.handler)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/infra/envs/dev/build"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

package() {
  local name="$1"
  shift
  local root="$BUILD_DIR/$name"
  local target="$root/services"
  mkdir -p "$target"
  cp "$ROOT/services/__init__.py" "$target/__init__.py"
  for pkg in "$@"; do
    cp -r "$ROOT/services/$pkg" "$target/$pkg"
  done
  # boto3/botocore are already in the Lambda Python runtime -- only bundle the
  # two things it doesn't provide. --no-deps keeps pip from also vendoring its
  # own botocore, which would just bloat the zip.
  pip install --no-cache-dir --no-deps --target "$root" aws-xray-sdk wrapt >/dev/null
  (cd "$root" && zip -qr "../$name.zip" .)
  echo "built $BUILD_DIR/$name.zip"
}

package trigger common trigger
package semaphore common semaphore
package session_profile common session_profile
