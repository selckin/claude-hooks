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
| `--pack` | `packs/peon` | Sound pack directory |
| `--config` | `sounds.conf` | Per-event overrides file |

```bash
./server.py --pack packs/glados --port 8080
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

The server loads a sound pack at startup, maps pack categories to Claude Code event names, and plays a random sound from the matching category on each request.

For `notification` and `permission_request` events, the server also sends a desktop notification via `notify-send`.

## Sound packs

Sound packs live in `packs/` and come from [openpeon](https://github.com/garysheng/openpeon). Each pack has a JSON manifest mapping categories to sound files.

Available packs:

`dota2_axe`, `duke_nukem`, `glados`, `hd2_helldiver`, `peasant`, `peon`, `ra2_kirov`, `sc_battlecruiser`, `sc_kerrigan`, `tf2_engineer`

### Category to event mapping

| Pack Category | Hook Events |
|---------------|-------------|
| session.start | session_start |
| task.acknowledge | user_prompt_submit |
| task.complete | stop, task_completed, subagent_stop |
| task.error | post_tool_use_failure |
| input.required | notification, permission_request |
| resource.limit | pre_compact |

## Configuration

### sounds.conf

Override or silence specific events. Applied after the pack is loaded.

Override an event with a specific file:

```
stop=/path/to/custom/sound.ogg
```

Silence an event (empty value):

```
pre_tool_use=
post_tool_use=
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
| `sounds.conf` | Per-event overrides and silencing |
| `claude-hooks.service` | systemd user service unit |
| `packs/` | Sound packs with openpeon JSON manifests |

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
