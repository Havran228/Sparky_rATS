#!/bin/sh
# Install the pinned Typst CLI into /usr/local/bin (ADR-009, ADR-023).
# Used by the backend image and by CI. Poppler and the fonts are apt packages.
set -eu

VERSION="${TYPST_VERSION:-0.15.1}"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) TARGET="x86_64-unknown-linux-musl" ;;
  aarch64 | arm64) TARGET="aarch64-unknown-linux-musl" ;;
  *)
    echo "unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

PREFIX="${PREFIX:-/usr/local}"
URL="https://github.com/typst/typst/releases/download/v${VERSION}/typst-${TARGET}.tar.xz"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

curl -fsSL "$URL" -o "$WORKDIR/typst.tar.xz"
tar -xJf "$WORKDIR/typst.tar.xz" -C "$WORKDIR"
install -m 0755 "$WORKDIR/typst-${TARGET}/typst" "$PREFIX/bin/typst"
"$PREFIX/bin/typst" --version
