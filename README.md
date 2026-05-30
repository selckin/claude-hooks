# claude-hooks

Audio and desktop notifications for Claude Code. A Python HTTP server runs outside the bubblewrap sandbox, and a shell hook script inside the sandbox curls it to trigger sounds and notifications on Claude Code events.

## Requirements

- Python 3.10+
- [mpv](https://mpv.io/) for audio playback
- `notify-send` for desktop notifications (optional, usually part of `libnotify`)

## Setup

### 1. Start the server

```bash
./server.py
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `7331` | Port to listen on |
| `--config` | `sounds.conf` | Event → sound mapping file |

```bash
./server.py --port 8080
```

### 2. Configure Claude Code hooks

Copy or symlink `settings.json` to `~/.claude/settings.json`:

```bash
ln -s ~/sources/claude-hooks/settings.json ~/.claude/settings.json
```

Or merge the `hooks` section into your existing settings. The hooks call `hook.sh` with an event name, which fires a curl request to the server.

### 3. (Optional) Run as a systemd user service

```bash
ln -s ~/sources/claude-hooks/claude-hooks.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-hooks
```

Check status:

```bash
systemctl --user status claude-hooks
journalctl --user -u claude-hooks -f
```

## How it works

```
Claude Code hook event
  -> hook.sh (curl, fire-and-forget)
    -> server.py (plays sound via mpv, optionally sends notify-send)
```

At startup the server reads the event → sound mapping from `sounds.conf`, then
plays a random sound from the matching event's list on each request.

For `notification` and `permission_request` events, the server also sends a desktop notification via `notify-send`.

## Sounds

The sound files live in `packs/peon/sounds/` (the Orc Peon pack from
[openpeon](https://github.com/garysheng/openpeon)). `sounds.conf` maps each
Claude Code hook event to the files it can play. The defaults that ship enabled:

| Hook Event | Peon lines |
|------------|------------|
| stop | "Ready to work?", "Something need doing?", "I can do that.", "Be happy to.", "Work, work.", "OK." |
| notification, permission_request | "Something need doing?", "Hmm?", "What you want?", "Yes?" |

`sounds.conf` also ships commented-out mappings for `session_start`,
`user_prompt_submit`, `subagent_stop`, `task_completed`, `post_tool_use_failure`,
and `pre_compact` — uncomment a line to enable that event.

## Configuration

### sounds.conf

The event → sound mapping. Each line is `EVENT=file[,file,...]`; one of the
listed files is picked at random per play. Bare filenames resolve against
`packs/peon/sounds/`, absolute paths are used as-is.

Map an event to one or more sounds:

```
stop=PeonReady1.wav,PeonYes1.wav
notification=/path/to/custom/sound.ogg
```

Silence an event (empty value), or comment it out to disable it entirely:

```
pre_tool_use=
#post_tool_use=...
```

### Custom port

Set `CLAUDE_AUDIO_PORT` in the hook script's environment to match the server port:

```bash
CLAUDE_AUDIO_PORT=8080 ./hook.sh notification
```

## Files

| File | Description |
|------|-------------|
| `server.py` | HTTP server, plays sounds via mpv, sends desktop notifications |
| `hook.sh` | Thin curl wrapper called by Claude Code hooks |
| `settings.json` | Claude Code hooks config (symlink to `~/.claude/settings.json`) |
| `sounds.conf` | Event → sound mapping (source of truth) |
| `claude-hooks.service` | systemd user service unit |
| `packs/peon/sounds/` | Orc Peon `.wav` files referenced by `sounds.conf` |

## API

```
GET /play?event=<name>
```

| Response | Meaning |
|----------|---------|
| 200 | Sound played (and notification sent if applicable) |
| 204 | Event unknown, unmapped, or silenced |
| 404 | Unknown path |
| 500 | mpv not installed |
