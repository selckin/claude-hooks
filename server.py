#!/usr/bin/env python3
"""Simple HTTP server that plays audio files on request.

Run this OUTSIDE bubblewrap. Claude hooks inside bubblewrap
will curl this server to trigger sounds.

Usage: ./server.py [--port 7331] [--config sounds.conf]
       ./server.py --hooks            # wire up Claude hooks, then exit
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# event -> list of audio file paths (randomly picked on each play)
sounds: dict[str, list[str]] = {}

# event -> desktop notification message body
notifications: dict[str, str] = {}

# Relative sound paths in sounds.conf resolve against this directory.
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")

# Claude Code hook events that --hooks wires up. Each fires
# `hook.sh <event>` using the event name verbatim.
HOOK_EVENTS = [
    "SessionStart", "SessionEnd", "Setup", "UserPromptSubmit",
    "Notification", "PermissionRequest", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "SubagentStart", "SubagentStop", "Stop",
    "TeammateIdle", "TaskCompleted", "PreCompact",
]

RATE_LIMIT = 5  # seconds between actions
_last_play = 0.0
_last_notify = 0.0


def load_config(path):
    """Load the event -> sound mapping from sounds.conf.

    Each line is `EVENT=file[,file...]`; one of the files is picked at random
    per play. Relative paths resolve against SOUNDS_DIR, absolute paths are
    used as-is. An empty value silences the event. Blank lines and lines
    starting with `#` are ignored, so commenting out an event disables it.
    """
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            files = []
            for item in value.split(","):
                item = item.strip()
                if not item:
                    continue
                resolved = item if os.path.isabs(item) else os.path.join(SOUNDS_DIR, item)
                if os.path.isfile(resolved):
                    files.append(resolved)
                else:
                    print(f"  warning: {key}: file not found: {resolved}", file=sys.stderr)
            sounds[key] = files


def load_notify_config(path):
    """Load the event -> notification message mapping from notify.conf.

    Each line is `EVENT=message`; the event fires a desktop notification with
    that message as the body (title is always "Claude Code"). An empty value
    silences it. Blank lines and lines starting with `#` are ignored, so
    commenting out an event disables its notification.
    """
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key:
                notifications[key] = value.strip()


def install_hooks(settings_path):
    """Wire every Claude hook event to hook.sh in the Claude settings file.

    Merges into the existing settings: other top-level keys and any hook
    events we don't manage are preserved; our event entries are replaced.
    """
    settings_path = os.path.abspath(os.path.expanduser(settings_path))
    hook_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.sh")

    entries = {}
    for name in HOOK_EVENTS:
        entries[name] = [{
            "matcher": "*",
            "hooks": [{
                "type": "command",
                "command": f"{hook_sh} {name}",
                "async": True,
            }],
        }]

    settings = {}
    if os.path.isfile(settings_path):
        with open(settings_path) as f:
            try:
                settings = json.load(f)
            except json.JSONDecodeError as e:
                print(f"error: {settings_path} is not valid JSON: {e}", file=sys.stderr)
                sys.exit(1)  # abort before overwriting the user's settings

    settings.setdefault("hooks", {}).update(entries)

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    if os.path.islink(settings_path):
        os.unlink(settings_path)  # replace a symlink with a real file
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"configured {len(entries)} hooks in {settings_path}")


def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"\033[90m{ts}\033[0m {msg}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/play":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        event = params.get("event", [None])[0]

        if not event:
            log(f"  \033[33mignored\033[0m  no event specified")
            self.send_response(204)
            self.end_headers()
            return

        sound_files = sounds.get(event)
        message = notifications.get(event)

        if not sound_files and not message:
            if event in sounds or event in notifications:
                log(f"  \033[90msilenced\033[0m {event}")
            else:
                log(f"  \033[33mignored\033[0m  {event} (unmapped)")
            self.send_response(204)
            self.end_headers()
            return

        global _last_play, _last_notify
        now = time.monotonic()
        actions = []
        mpv_missing = False

        if sound_files:
            if now - _last_play >= RATE_LIMIT:
                _last_play = now
                path = random.choice(sound_files)
                try:
                    subprocess.Popen(
                        ["mpv", "--no-video", "--really-quiet", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    actions.append(f"\033[32mplay\033[0m     {event} -> {os.path.basename(path)}")
                except FileNotFoundError:
                    # Don't let a missing mpv suppress the notification below.
                    mpv_missing = True
                    actions.append(f"\033[31merror\033[0m    {event}: mpv not installed")
            else:
                actions.append(f"\033[90mthrottle\033[0m {event} (audio)")

        if message:
            if now - _last_notify >= RATE_LIMIT:
                _last_notify = now
                subprocess.Popen(
                    ["notify-send", "Claude Code", message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                actions.append(f"\033[34mnotify\033[0m   {event} -> {message}")
            else:
                actions.append(f"\033[90mthrottle\033[0m {event} (notify)")

        for action in actions:
            log(f"  {action}")

        if mpv_missing:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"mpv not installed\n")
        else:
            self.send_response(200)
            self.end_headers()

    def log_message(self, *args, **kwargs):
        pass


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Audio notification server")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument(
        "--config",
        default=os.path.join(script_dir, "sounds.conf"),
        help="Path to sounds.conf (event -> sound mapping)",
    )
    parser.add_argument(
        "--notify-config",
        default=os.path.join(script_dir, "notify.conf"),
        help="Path to notify.conf (event -> notification message)",
    )
    parser.add_argument(
        "--hooks",
        nargs="?",
        const="~/.claude/settings.json",
        default=None,
        metavar="SETTINGS",
        help="Install hook entries into the Claude settings file "
             "(default: ~/.claude/settings.json), then exit",
    )
    args = parser.parse_args()

    if args.hooks:
        install_hooks(args.hooks)
        return

    if os.path.isfile(args.config):
        print(f"config: {args.config}")
        load_config(args.config)
    else:
        print(f"warning: config not found: {args.config}", file=sys.stderr)

    if os.path.isfile(args.notify_config):
        print(f"notify config: {args.notify_config}")
        load_notify_config(args.notify_config)

    active_sounds = {k: len(v) for k, v in sounds.items() if v}
    active_notify = sorted(k for k, v in notifications.items() if v)
    print(f"sounds: {active_sounds}")
    print(f"notifications: {active_notify}")

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
