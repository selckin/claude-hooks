#!/usr/bin/env bash
# Claude Code hook script - sends a request to the audio server.
# Called by Claude hooks with the event name as $1.
#
# Usage: hook.sh <event>
#   event: "interaction" or "done"

PORT="${CLAUDE_AUDIO_PORT:-7331}"
EVENT="$1"

# Fire and forget - don't let network issues block Claude
curl -s --max-time 1 "http://127.0.0.1:${PORT}/play?event=${EVENT}" >/dev/null 2>&1 &
