#!/usr/bin/env bash
# =============================================================================
# scripts/deploy_demo.sh — publish the static demo to GitHub Pages.
#
#   bash scripts/deploy_demo.sh            # after: python scripts/build_demo_snapshot.py
#
# Builds the Next.js UI in demo mode (static HTML + the JSON snapshot in
# ui/public/demo/) and force-pushes it to the `gh-pages` branch.
# Live at: https://tanmay1504.github.io/health-genai-project/
# Uses the GitHub CLI's login for the push; no token is written anywhere.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="Tanmay1504/health-genai-project"
BASE_PATH="/health-genai-project"

[ -d "$ROOT/ui/public/demo" ] || { echo "No snapshot. Run: python scripts/build_demo_snapshot.py" >&2; exit 1; }

cd "$ROOT/ui"
rm -rf out .next
DEMO_EXPORT=1 NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH="$BASE_PATH" NEXT_TELEMETRY_DISABLED=1 npm run build
touch out/.nojekyll   # otherwise GitHub Pages hides the _next/ folder

# Push out/ as the whole content of the gh-pages branch (a throwaway repo, so
# the main branch history stays clean).
cd out
git init -q -b gh-pages
git add -A
git -c user.name="$(git -C "$ROOT" config user.name)" -c user.email="$(git -C "$ROOT" config user.email)" \
    commit -q -m "Deploy static demo"
git -c credential.helper= -c credential.helper='!gh auth git-credential' \
    push -q -f "https://github.com/$REPO.git" gh-pages
rm -rf .git

# Turn on Pages for the branch (first time only; harmless if already on).
gh api -X POST "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null 2>&1 || true
echo "Deployed: https://tanmay1504.github.io$BASE_PATH/"
