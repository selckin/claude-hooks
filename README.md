# claude-hooks

Audio and desktop notifications for Claude Code. A Python HTTP server runs outside the bubblewrap sandbox, and a shell hook script inside the sandbox curls it to trigger sounds and notifications on Claude Code events.

A simple version of (and inspired by) [peon-ping](https://github.com/PeonPing/peon-ping).

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
| `--notify-config` | `notify.conf` | Event → notification message file |

```bash
./server.py --port 8080
```

### 2. Configure Claude Code hooks

```bash
./server.py --hooks
```

This wires every Claude Code hook event to `hook.sh` in `~/.claude/settings.json`
(merging into your existing settings — other keys are left untouched), then exits.
Pass a path to target a different settings file: `./server.py --hooks /path/to/settings.json`.

Each hook calls `hook.sh` with an event name, which fires a curl request to the server.

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

At startup the server reads the event → sound mapping from `sounds.conf` and the
event → notification mapping from `notify.conf`. On each request it plays a
random sound for the event (if mapped) and/or sends a desktop notification (if
mapped) via `notify-send`. The two are independent — an event can have a sound,
a notification, both, or neither.

## Sounds

The sound files live in `packs/peon/sounds/` (the Orc Peon pack from
[openpeon](https://github.com/garysheng/openpeon)). `sounds.conf` maps each
Claude Code hook event to the files it can play. The defaults that ship enabled:

| Hook Event | Peon lines |
|------------|------------|
| Stop | "Ready to work?", "Something need doing?", "I can do that.", "Be happy to.", "Work, work.", "OK." |
| Notification, PermissionRequest | "Something need doing?", "Hmm?", "What you want?", "Yes?" |

`sounds.conf` also ships commented-out mappings for `SessionStart`,
`UserPromptSubmit`, `SubagentStop`, `TaskCompleted`, `PostToolUseFailure`,
and `PreCompact` — uncomment a line to enable that event.

## Configuration

### sounds.conf

The event → sound mapping. Each line is `EVENT=file[,file,...]`; one of the
listed files is picked at random per play. Bare filenames resolve against
`packs/peon/sounds/`, absolute paths are used as-is.

Map an event to one or more sounds:

```
Stop=PeonReady1.wav,PeonYes1.wav
Notification=/path/to/custom/sound.ogg
```

Silence an event (empty value), or comment it out to disable it entirely:

```
PreToolUse=
#PostToolUse=...
```

### notify.conf

The event → desktop notification mapping, same conventions as `sounds.conf`.
Each line is `EVENT=message`; the event fires a `notify-send` notification with
that message as the body (title is always "Claude Code"). Empty value silences,
commenting out disables. `Notification` and `PermissionRequest` ship enabled.

```
Notification=Claude has a notification
#Stop=Claude is done
```

### Custom port

Set `CLAUDE_AUDIO_PORT` in the hook script's environment to match the server port:

```bash
CLAUDE_AUDIO_PORT=8080 ./hook.sh Notification
```

## Files

| File | Description |
|------|-------------|
| `server.py` | HTTP server (plays sounds, sends notifications); `--hooks` installs the Claude Code hooks |
| `hook.sh` | Thin curl wrapper called by Claude Code hooks |
| `sounds.conf` | Event → sound mapping |
| `notify.conf` | Event → desktop notification mapping |
| `claude-hooks.service` | systemd user service unit |
| `packs/peon/sounds/` | Orc Peon `.wav` files referenced by `sounds.conf` |

## API

```
GET /play?event=<name>
```

| Response | Meaning |
|----------|---------|
| 200 | Sound and/or notification fired |
| 204 | Event has no sound and no notification (unmapped or silenced) |
| 404 | Unknown path |
| 500 | mpv not installed (the notification, if any, is still sent) |
