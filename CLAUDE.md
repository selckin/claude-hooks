# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Audio and desktop notification system for Claude Code hooks. A Python HTTP server runs **outside** the bubblewrap sandbox, and a shell hook script inside the sandbox curls it to trigger sounds on Claude Code events.

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- [mpv](https://mpv.io/) for audio playback
- `notify-send` (optional, for desktop notifications on `notification`/`permission_request` events)

## Architecture

```
Claude Code hook event
  -> hook.sh (curl, fire-and-forget)
    -> server.py (plays sound via mpv, optionally sends notify-send)
```

- **`server.py`** — HTTP server on `127.0.0.1:7331`. Loads the event → sound mapping from `sounds.conf` at startup, plays a random matching sound on `GET /play?event=<name>`. Desktop notifications sent for events in `NOTIFY_EVENTS`.
- **`hook.sh`** — Thin curl wrapper. Receives event name as `$1`, fires curl in background. Port configurable via `CLAUDE_AUDIO_PORT` env var (default 7331).
- **`--hooks`** — `server.py --hooks` writes a `hook.sh <event>` entry for every Claude hook event (`HOOK_EVENTS`) into `~/.claude/settings.json`, merging into existing settings, then exits. Replaces the old hand-maintained `settings.json`.
- **`sounds.conf`** — The event → sound mapping (single source of truth). `EVENT=file[,file,...]` maps an event to one or more sounds (one picked at random); bare filenames resolve against `packs/peon/sounds/`, absolute paths used as-is. `EVENT=` (empty) silences an event; a commented-out (`#`) line disables it.
- **`packs/peon/sounds/`** — The Orc Peon `.wav` sound files (from [openpeon](https://github.com/garysheng/openpeon)) referenced by `sounds.conf`.
- **`claude-hooks.service`** — systemd user service unit.

## Running

```bash
./server.py                          # default: port 7331, sounds.conf
./server.py --port 8080              # custom port
./server.py --config other.conf      # custom mapping file
./server.py --hooks                  # install hooks into ~/.claude/settings.json, then exit

# As systemd service:
systemctl --user enable --now claude-hooks
journalctl --user -u claude-hooks -f
```

## Key Design Details

- Sound selection is random from the matching event's list (`random.choice`)
- `sounds` dict is the single source of truth: `event_name -> list[filepath]`. Empty list = silenced, missing key = unmapped/ignored.
- `load_config` populates the global `sounds` dict from `sounds.conf`. Relative paths resolve against `SOUNDS_DIR` (`packs/peon/sounds/`); missing files are warned about and dropped.
- All mpv/notify-send calls are fire-and-forget via `subprocess.Popen` (non-blocking).
- Server suppresses default HTTP request logging (`log_message` is a no-op); uses custom `log()` with timestamps and ANSI colors.
