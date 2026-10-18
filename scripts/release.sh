
#!/usr/bin/env bash
set -e

VERSION_ARG="$1"
MANIFEST="custom_components/fluks/manifest.json"

if [ -z "$VERSION_ARG" ]; then
    echo "Usage: $0 <major|minor|patch|version>"
    echo "Example: $0 patch"
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

manifest["version"] = version

with open(path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")

print(version)
PY
)

git add "$MANIFEST"
git commit -m "chore: release v$VERSION"
git tag "v$VERSION"

echo "Created release v$VERSION"
echo "Push with:"
echo "  git push && git push origin v$VERSION"
