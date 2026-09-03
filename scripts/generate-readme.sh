#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

{
  echo "<!-- AUTO-GENERATED from artifact.md by scripts/generate-readme.sh. Do not edit directly. -->"
  echo
  cat artifact.md
} > README.md
