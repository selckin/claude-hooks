#!/usr/bin/env python3
"""Simple HTTP server that plays audio files on request.

Run this OUTSIDE bubblewrap. Claude hooks inside bubblewrap
will curl this server to trigger sounds.

Usage: ./server.py [--port 7331] [--pack packs/peon] [--config sounds.conf]
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

# Map pack categories to hook event names
CATEGORY_MAP = {
    "session.start": ["session_start"],
    "task.acknowledge": ["user_prompt_submit"],
    "task.complete": ["stop", "task_completed", "subagent_stop"],
    "task.error": ["post_tool_use_failure"],
    "input.required": ["notification", "permission_request"],
    "resource.limit": ["pre_compact"],
    "user.spam": [],
}

NOTIFY_EVENTS = {"notification", "permission_request"}

RATE_LIMIT = 5  # seconds between actions
_last_play = 0.0
_last_notify = 0.0


def load_pack(pack_dir):
    """Load a sound pack directory containing an openpeon.json manifest."""
    manifest = None
    for name in os.listdir(pack_dir):
        if name.endswith(".json"):
            with open(os.path.join(pack_dir, name)) as f:
                manifest = json.load(f)
            break
    if not manifest:
        print(f"error: no JSON manifest found in {pack_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"pack: {manifest.get('display_name', manifest.get('name', '?'))}")

    for category, data in manifest.get("categories", {}).items():
        files = []
        for entry in data.get("sounds", []):
            path = os.path.join(pack_dir, entry["file"])
            if os.path.isfile(path):
                files.append(path)
            else:
                print(f"  warning: {entry['file']}: not found", file=sys.stderr)

        hook_events = CATEGORY_MAP.get(category, [])
        if not hook_events:
            print(f"  {category}: {len(files)} sounds (unmapped)")
            continue

        for event in hook_events:
            sounds.setdefault(event, []).extend(files)
        print(f"  {category} -> {', '.join(hook_events)}: {len(files)} sounds")


def load_config(path):
    """Load sounds.conf overrides. A single file per event (overrides pack)."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key:
                if not value:
                    sounds[key] = []  # silence this event
                elif not os.path.isfile(value):
                    print(f"  warning: {key}: file not found: {value}", file=sys.stderr)
                else:
                    sounds[key] = [value]  # override with single file


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

        if event in sounds and not sounds[event]:
            log(f"  \033[90msilenced\033[0m {event}")
            self.send_response(204)
            self.end_headers()
            return

        if event not in sounds:
            log(f"  \033[33mignored\033[0m  {event} (unmapped)")
            self.send_response(204)
            self.end_headers()
            return

        global _last_play, _last_notify
        now = time.monotonic()

        files = sounds[event]
        path = random.choice(files)
        filename = os.path.basename(path)
        actions = []

        if now - _last_play >= RATE_LIMIT:
            _last_play = now
            try:
                subprocess.Popen(
                    ["mpv", "--no-video", "--really-quiet", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                log(f"  \033[31merror\033[0m    {event}: mpv not installed")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"mpv not installed\n")
                return
            actions.append(f"\033[32mplay\033[0m     {event} -> {filename}")
        else:
            actions.append(f"\033[90mthrottle\033[0m {event} (audio)")

        if event in NOTIFY_EVENTS:
            if now - _last_notify >= RATE_LIMIT:
                _last_notify = now
                subprocess.Popen(
                    ["notify-send", "Claude Code", event],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                actions.append(f"\033[34mnotify\033[0m   {event}")
            else:
                actions.append(f"\033[90mthrottle\033[0m {event} (notify)")

        for action in actions:
            log(f"  {action}")

        self.send_response(200)
        self.end_headers()

    def log_message(self, *args, **kwargs):
        pass


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Audio notification server")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument(
        "--pack",
        default=os.path.join(script_dir, "packs", "peon"),
        help="Path to a sound pack directory (default: packs/peon)",
    )
    parser.add_argument(
        "--config",
        default=os.path.join(script_dir, "sounds.conf"),
        help="Path to sounds.conf overrides (applied after pack)",
    )
    args = parser.parse_args()

    load_pack(args.pack)

    if os.path.isfile(args.config):
        print(f"config: {args.config}")
        load_config(args.config)

    active = {k: len(v) for k, v in sounds.items() if v}
    print(f"active events: {active}")

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
