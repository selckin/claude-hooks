# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Audio and desktop notification system for Claude Code hooks. A Python HTTP server runs **outside** the bubblewrap sandbox, and a shell hook script inside the sandbox curls it to trigger sounds on Claude Code events.

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- [mpv](https://mpv.io/) for audio playback
- `notify-send` (optional, for desktop notifications on `Notification`/`PermissionRequest` events)

## Architecture

```
Claude Code hook event
  -> hook.sh (curl, fire-and-forget)
    -> server.py (plays sound via mpv, optionally sends notify-send)
```

- **`server.py`** — HTTP server on `127.0.0.1:7331`. Loads the event → sound mapping from `sounds.conf` and the event → notification mapping from `notify.conf` at startup. On `GET /play?event=<name>` it plays a random matching sound and/or sends a desktop notification (`notify-send`) — the two are independent.
- **`hook.sh`** — Thin curl wrapper. Receives event name as `$1`, fires curl in background. Port configurable via `CLAUDE_AUDIO_PORT` env var (default 7331).
- **`--hooks`** — `server.py --hooks` writes a `hook.sh <event>` entry for every Claude hook event (`HOOK_EVENTS`) into `~/.claude/settings.json`, merging into existing settings, then exits. Replaces the old hand-maintained `settings.json`.
- **`sounds.conf`** — The event → sound mapping. `EVENT=file[,file,...]` maps an event to one or more sounds (one picked at random); bare filenames resolve against `sounds/`, absolute paths used as-is. `EVENT=` (empty) silences an event; a commented-out (`#`) line disables it.
- **`notify.conf`** — The event → desktop notification mapping (same conventions as `sounds.conf`). `EVENT=message` fires a `notify-send` notification with that body (title always "Claude Code"). `EVENT=` silences; commenting out disables.
- **`sounds/`** — The Orc Peon `.wav` sound files (from [openpeon](https://github.com/garysheng/openpeon)) referenced by `sounds.conf`.
- **`claude-hooks.service`** — systemd user service unit.

## Running

```bash
./server.py                          # default: port 7331, sounds.conf, notify.conf
./server.py --port 8080              # custom port
./server.py --config other.conf      # custom sound mapping file
./server.py --notify-config n.conf   # custom notification mapping file
./server.py --hooks                  # install hooks into ~/.claude/settings.json, then exit

# As systemd service:
systemctl --user enable --now claude-hooks
journalctl --user -u claude-hooks -f
```

## Key Design Details

- Sound selection is random from the matching event's list (`random.choice`)
- Two parallel dicts drive playback: `sounds` (`event -> list[filepath]`) and `notifications` (`event -> message`). For each, empty value = silenced, missing key = unmapped/ignored. A request acts on whichever the event appears in; if neither, it's a 204.
- `load_config` populates `sounds` from `sounds.conf` (relative paths resolve against `SOUNDS_DIR` (`sounds/`); missing files are warned about and dropped). `load_notify_config` populates `notifications` from `notify.conf`.
- Audio and notifications throttle independently (`_last_play` vs `_last_notify`, `RATE_LIMIT` seconds each).
- All mpv/notify-send calls are fire-and-forget via `subprocess.Popen` (non-blocking).
- Server suppresses default HTTP request logging (`log_message` is a no-op); uses custom `log()` with timestamps and ANSI colors.
