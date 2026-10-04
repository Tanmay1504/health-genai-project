#!/usr/bin/env bash
# =============================================================================
# data/fetch_synthea.sh — download Synthea and generate synthetic FHIR patients.
#
# Usage:   bash data/fetch_synthea.sh [N_PATIENTS] [SEED]
#          bash data/fetch_synthea.sh 250 42
#
# Requires Java 17+. macOS ships/old installs often have Java 8, which
# Synthea rejects. Install with:  brew install openjdk@17
# Java lookup order: $JAVA, $JAVA_HOME/bin/java, Homebrew openjdk@17,
# ~/.local/opt/jdk-21 (the no-Homebrew install), then `java` on PATH.
#
# Output: data/synthea_output/fhir/
#         <Patient>.json, hospitalInformation*.json, practitionerInformation*.json
# =============================================================================
set -euo pipefail

N="${1:-250}"
SEED="${2:-42}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/data/tools"
OUT="$ROOT/data/synthea_output"
JAR="$TOOLS/synthea-with-dependencies.jar"
URL="https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar"

# --- pick a Java >= 17 -------------------------------------------------------
if [ -n "${JAVA:-}" ]; then :
elif [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then JAVA="$JAVA_HOME/bin/java"
elif command -v brew >/dev/null 2>&1 && [ -x "$(brew --prefix openjdk@17 2>/dev/null)/bin/java" ]; then
  JAVA="$(brew --prefix openjdk@17)/bin/java"
elif [ -x "$HOME/.local/opt/jdk-21/Contents/Home/bin/java" ]; then JAVA="$HOME/.local/opt/jdk-21/Contents/Home/bin/java"
else JAVA="java"
fi
JV=$("$JAVA" -version 2>&1 | awk -F'"' '/version/ {print $2}')
MAJOR="${JV%%.*}"; [ "$MAJOR" = "1" ] && MAJOR=$(echo "$JV" | cut -d. -f2)
if [ "${MAJOR:-0}" -lt 17 ]; then
  echo "ERROR: Synthea needs Java 17+, found $JV ($JAVA)."
  echo "       brew install openjdk@17   (then re-run this script)"
  echo "       ...or skip Synthea:  python -m data.generate_synthetic_fhir"
  exit 1
fi

# --- download once -------------------------------------------------------------
mkdir -p "$TOOLS"
if [ ! -f "$JAR" ]; then
  echo "Downloading Synthea (~150 MB) ..."
  # -C - resumes a partial download; --retry survives flaky connections.
  curl -L --fail -C - --retry 5 --retry-delay 3 -o "$JAR.part" "$URL" && mv "$JAR.part" "$JAR"
fi

# --- generate ------------------------------------------------------------------
# -s seed           : reproducible population
# -p N              : number of (living) patients
# years_of_history  : keep only recent history -> manageable claim volume
# hospital/practitioner export: write shared Organization/Practitioner files,
#   which the parser resolves via conditional references.
rm -rf "$OUT"
echo "Generating $N patients with seed $SEED using $JAVA ..."
"$JAVA" -jar "$JAR" -s "$SEED" -p "$N" \
  --exporter.baseDirectory "$OUT" \
  --exporter.years_of_history 5 \
  --exporter.fhir.export true \
  --exporter.hospital.fhir.export true \
  --exporter.practitioner.fhir.export true \
  --exporter.csv.export false \
  --exporter.ccda.export false \
  Massachusetts

echo "Done: $(ls "$OUT/fhir" | wc -l | tr -d ' ') FHIR files in $OUT/fhir"
