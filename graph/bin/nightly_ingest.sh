#!/usr/bin/env bash
# Scheduled re-ingest for the JAY-OS Graph Integration Engine.
#
# WHY A SHELL SCRIPT + USER CRONTAB, NOT A DOCKER SERVICE
# ---------------------------------------------------------
# email-sentinel's own services run as `while true; do cmd; sleep N; done`
# inside docker-compose.yml -- but that is a tracked PRODUCTION file, and
# Phase 1 of this build was explicit: no production file gets edited to make
# the graph engine work. Adding a service there would be exactly that edit.
#
# The graph engine also doesn't need a container: it's pure-stdlib Python
# (the one exception, SentinelMatchedEmails, shells out to `docker exec`
# itself). A long-running container that sleeps 23.9 hours a day to do one
# batch job is also just a worse fit than a once-a-night cron entry.
#
# So: a user crontab entry (no sudo, reversible with `crontab -e`), calling
# this script, which does the real work and never throws output away.
#
# WHY THIS NEVER EXITS NON-ZERO
# -------------------------------
# A cron job whose failure is silent is worse than no automation -- the
# exact failure mode `email_sentinel`'s own README was written to prevent
# (637 N Riata St, 72 days). Every step is logged with a timestamp and a
# PASS/FAIL marker; a human (or a future health check) greps the log, rather
# than relying on cron's own mail delivery, which is not configured on this
# box. The script still exits 0 so cron does not retry or spam -- the log
# IS the alerting surface.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$REPO_ROOT/graph/logs"
LOG_FILE="$LOG_DIR/ingest.log"
mkdir -p "$LOG_DIR"

# Keep the log from growing forever. 2MB is generous for a nightly summary.
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 2097152 ]; then
    mv "$LOG_FILE" "$LOG_FILE.1"
fi

log() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG_FILE"; }

cd "$REPO_ROOT" || { log "FAIL  could not cd to $REPO_ROOT"; exit 0; }

log "=== nightly re-ingest starting ==="

run_step() {
    local label="$1"; shift
    if output=$(python3 -m graph "$@" 2>&1); then
        log "PASS  $label"
        printf '%s\n' "$output" | sed 's/^/    /' >> "$LOG_FILE"
    else
        log "FAIL  $label (exit $?)"
        printf '%s\n' "$output" | sed 's/^/    /' >> "$LOG_FILE"
    fi
}

run_step "ingest projects_json + permit_folders"  ingest --projects 0 --folders
run_step "ingest sentinel contacts + matched emails" ingest --contacts --matched-emails
run_step "lesson generation"                      lessons
run_step "health report"                          health

log "=== nightly re-ingest finished ==="
exit 0
