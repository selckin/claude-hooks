#!/usr/bin/env bash
# Claude Code hook script - sends a request to the audio server.
# Called by Claude hooks with the event name as $1; the hook's JSON payload
# arrives on stdin and is forwarded to the server (for the SQLite hook log).
#
# Usage: hook.sh <event>

PORT="${CLAUDE_AUDIO_PORT:-7331}"
EVENT="$1"

# Capture the hook payload from stdin before backgrounding the curl.
PAYLOAD="$(cat)"

# Fire and forget - don't let network issues block Claude.
curl -s --max-time 2 -X POST -H 'Content-Type: application/json' \
    --data-binary "$PAYLOAD" \
    "http://127.0.0.1:${PORT}/play?event=${EVENT}" >/dev/null 2>&1 &
