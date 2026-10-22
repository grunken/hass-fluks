#!/usr/bin/env bash
set -euo pipefail

VERSION_ARG="${1:-}"
MANIFEST="custom_components/fluks/manifest.json"

if [ -z "$VERSION_ARG" ]; then
    echo "Usage: $0 <major|minor|patch|version>"
    echo "Example: $0 patch"
    exit 1
fi

# Validate prerequisites before modifying anything.
for command in git python3 gh; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Error: $command is required."
        exit 1
    fi
done

if ! gh auth status >/dev/null 2>&1; then
    echo "Error: GitHub CLI is not authenticated."
    echo "Run: gh auth login"
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Error: Working tree is not clean."
    echo "Commit or stash changes before releasing."
    exit 1
fi

BRANCH=$(git branch --show-current)

if [ "$BRANCH" != "main" ]; then
    echo "Error: Releases must be created from main."
    exit 1
fi

git fetch origin main --quiet

if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "Error: Local main differs from origin/main."
    echo "Synchronize the branch before releasing."
    exit 1
fi

VERSION=$(python3 - "$MANIFEST" "$VERSION_ARG" <<'PY'
import json
import re
import sys

path, arg = sys.argv[1:]

with open(path, "r", encoding="utf-8") as f:
    manifest = json.load(f)

current = manifest["version"]

if arg in ("major", "minor", "patch"):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current)
    if not match:
        sys.exit(f"Invalid current version: {current}")

    major, minor, patch = map(int, match.groups())

    if arg == "major":
        major, minor, patch = major + 1, 0, 0
    elif arg == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1

    version = f"{major}.{minor}.{patch}"
else:
    if not re.fullmatch(r"\d+\.\d+\.\d+", arg):
        sys.exit(f"Invalid version or bump type: {arg}")
    version = arg

print(version)
PY
)

TAG="v$VERSION"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "Error: Tag $TAG already exists locally."
    exit 1
fi

if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
    echo "Error: Tag $TAG already exists on origin."
    exit 1
fi

if gh release view "$TAG" >/dev/null 2>&1; then
    echo "Error: GitHub Release $TAG already exists."
    exit 1
fi

# Update manifest version.
python3 - "$MANIFEST" "$VERSION" <<'PY'
import json
import sys

path, version = sys.argv[1:]

with open(path, "r", encoding="utf-8") as f:
    manifest = json.load(f)

manifest["version"] = version

with open(path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY

# Commit and tag.
git add "$MANIFEST"
git commit -m "chore: release $TAG"
git tag "$TAG"

# Push commit and tag.
git push origin main
git push origin "$TAG"

# Publish GitHub Release.
gh release create "$TAG" \
    --title "Fluks for Home Assistant $VERSION" \
    --generate-notes \
    --latest

echo
echo "Successfully published Fluks $VERSION"
echo "GitHub Release:"
gh release view "$TAG" --json url --jq '.url'

