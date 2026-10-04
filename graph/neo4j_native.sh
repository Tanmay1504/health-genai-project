#!/usr/bin/env bash
# =============================================================================
# graph/neo4j_native.sh — run Neo4j WITHOUT Docker (start | stop | status).
#
# For Macs where Docker isn't an option (e.g. macOS 13: current Docker Desktop
# no longer supports it, and Homebrew has no prebuilt packages for it).
# Expects the tarball install described in MASTER_README §3, "No-Docker route":
#     ~/.local/opt/jdk-21     Java 21 (Neo4j 5.26 supports 17 and 21)
#     ~/.local/opt/neo4j      Neo4j 5.26 community + GDS jar in plugins/
# Override locations with NEO4J_HOME / JAVA_HOME if you installed elsewhere.
# Java is set only inside this script, so your system default Java is untouched.
# =============================================================================
set -euo pipefail
export JAVA_HOME="${JAVA_HOME_NEO4J:-$HOME/.local/opt/jdk-21/Contents/Home}"
NEO4J_HOME="${NEO4J_HOME:-$HOME/.local/opt/neo4j}"

if [ ! -x "$NEO4J_HOME/bin/neo4j" ]; then
  echo "Neo4j not found at $NEO4J_HOME. See MASTER_README.md §3 (No-Docker route)." >&2
  exit 1
fi

case "${1:-status}" in
  start)
    "$NEO4J_HOME/bin/neo4j" start
    echo "waiting for Neo4j..."; for _ in $(seq 1 60); do curl -s localhost:7474 >/dev/null && break; sleep 2; done
    echo "Neo4j up: http://localhost:7474" ;;
  stop)   "$NEO4J_HOME/bin/neo4j" stop ;;
  status) "$NEO4J_HOME/bin/neo4j" status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
