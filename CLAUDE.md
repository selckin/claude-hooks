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

- **`server.py`** — HTTP server on `127.0.0.1:7331`. Loads a sound pack at startup, maps openpeon categories to Claude hook event names via `CATEGORY_MAP`, plays a random matching sound on `GET /play?event=<name>`. Desktop notifications sent for events in `NOTIFY_EVENTS`.
- **`hook.sh`** — Thin curl wrapper. Receives event name as `$1`, fires curl in background. Port configurable via `CLAUDE_AUDIO_PORT` env var (default 7331).
- **`settings.json`** — Claude Code hooks config. Symlink to `~/.claude/settings.json`. Maps hook events to `hook.sh` invocations.
- **`sounds.conf`** — Per-event overrides applied after pack loading. `EVENT=/path/to/file` overrides, `EVENT=` (empty) silences an event. Comments with `#`.
- **`packs/`** — Sound packs from [openpeon](https://github.com/garysheng/openpeon). Each has an `openpeon.json` manifest mapping categories to sound files.
- **`claude-hooks.service`** — systemd user service unit.

## Running

```bash
./server.py                          # default: packs/peon on port 7331
./server.py --pack packs/peasant     # different sound pack
./server.py --port 8080              # custom port

# As systemd service:
systemctl --user enable --now claude-hooks
journalctl --user -u claude-hooks -f
```

## Key Design Details

- Sound selection is random from matching category (`random.choice`)
- `sounds` dict is the single source of truth: `event_name -> list[filepath]`. Empty list = silenced, missing key = unmapped/ignored.
- Pack loading (`load_pack`) and config overrides (`load_config`) both mutate the global `sounds` dict. Config always wins over pack.
- All mpv/notify-send calls are fire-and-forget via `subprocess.Popen` (non-blocking).
- Server suppresses default HTTP request logging (`log_message` is a no-op); uses custom `log()` with timestamps and ANSI colors.
