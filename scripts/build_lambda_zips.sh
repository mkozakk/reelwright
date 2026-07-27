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
  local target="$BUILD_DIR/$name/services"
  mkdir -p "$target"
  cp "$ROOT/services/__init__.py" "$target/__init__.py"
  for pkg in "$@"; do
    cp -r "$ROOT/services/$pkg" "$target/$pkg"
  done
  (cd "$BUILD_DIR/$name" && zip -qr "../$name.zip" .)
  echo "built $BUILD_DIR/$name.zip"
}

package trigger common trigger
package semaphore common semaphore
