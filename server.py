#!/usr/bin/env python3
"""Simple HTTP server that plays audio files on request.

Run this OUTSIDE bubblewrap. Claude hooks inside bubblewrap
will curl this server to trigger sounds.

Usage: ./server.py [--port 7331] [--config sounds.conf]
"""

import argparse
import os
import random
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# event -> list of audio file paths (randomly picked on each play)
sounds: dict[str, list[str]] = {}

# Relative sound paths in sounds.conf resolve against this directory.
SOUNDS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "packs", "peon", "sounds"
)

NOTIFY_EVENTS = {"notification", "permission_request"}

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
        "--config",
        default=os.path.join(script_dir, "sounds.conf"),
        help="Path to sounds.conf (event -> sound mapping)",
    )
    args = parser.parse_args()

    if os.path.isfile(args.config):
        print(f"config: {args.config}")
        load_config(args.config)
    else:
        print(f"warning: config not found: {args.config}", file=sys.stderr)

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
