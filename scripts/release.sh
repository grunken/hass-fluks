#!/usr/bin/env bash
set -e

VERSION="$1"

if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 1.0.2"
    exit 1
fi

MANIFEST="custom_components/fluks/manifest.json"

python3 - "$MANIFEST" "$VERSION" <<'PY'
import json
import sys

path = sys.argv[1]
version = sys.argv[2]

with open(path, "r", encoding="utf-8") as f:
    manifest = json.load(f)

manifest["version"] = version

with open(path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY

git add "$MANIFEST"
git commit -m "chore: release v$VERSION"
git tag "v$VERSION"

echo "Created release v$VERSION"
echo "Push with:"
echo "  git push && git push origin v$VERSION"