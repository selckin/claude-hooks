#!/usr/bin/env python3
"""Simple HTTP server that plays audio files on request.

Run this OUTSIDE bubblewrap. Claude hooks inside bubblewrap
will curl this server to trigger sounds.

Usage: ./server.py [--port 7331] [--config sounds.conf]
       ./server.py --hooks            # wire up Claude hooks, then exit
"""

import argparse
import datetime
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# event -> list of audio file paths (randomly picked on each play)
sounds: dict[str, list[str]] = {}

# event -> desktop notification message body
notifications: dict[str, str] = {}

# Relative sound paths in sounds.conf resolve against this directory.
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")

# Every Claude Code hook event, wired up by --hooks. Each fires
# `hook.sh <event>` using the event name verbatim. This is the full set
# documented at https://code.claude.com/docs/en/hooks.md — Claude Code has no
# event-name wildcard, so each event must be listed explicitly; add any newly
# introduced event here. Events with no sound/notification mapping are still
# logged to SQLite and otherwise no-op, so listing them all is free.
HOOK_EVENTS = [
    # Session lifecycle
    "SessionStart", "Setup", "SessionEnd",
    # User input
    "UserPromptSubmit", "UserPromptExpansion",
    # Tool execution
    "PreToolUse", "PermissionRequest", "PermissionDenied",
    "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    # Response / completion
    "Stop", "StopFailure",
    # Notification / display
    "Notification", "MessageDisplay",
    # Subagents / tasks
    "SubagentStart", "SubagentStop", "TaskCreated", "TaskCompleted",
    "TeammateIdle",
    # Configuration / filesystem
    "InstructionsLoaded", "ConfigChange", "CwdChanged", "FileChanged",
    # Worktrees
    "WorktreeCreate", "WorktreeRemove",
    # Context compaction
    "PreCompact", "PostCompact",
    # MCP elicitation
    "Elicitation", "ElicitationResult",
]

RATE_LIMIT = 5  # seconds between actions
_last_play = 0.0
_last_notify = 0.0

# Console verbosity. "default" logs only real actions (play/notify/error);
# "verbose" also logs hooks that take no action (silenced, unmapped,
# throttled). Overridable via --log-level or CLAUDE_HOOKS_LOG_LEVEL.
LOG_LEVELS = ("default", "verbose")
_env_level = os.environ.get("CLAUDE_HOOKS_LOG_LEVEL", "default")
if _env_level not in LOG_LEVELS:
    print(f"warning: ignoring invalid CLAUDE_HOOKS_LOG_LEVEL={_env_level!r}, "
          f"using 'default'", file=sys.stderr)
    _env_level = "default"
LOG_LEVEL = _env_level

# SQLite log of every hook call (read by the waybar claude-hooks module).
# Lives under ~/.claude so it's reachable from both the server and the host.
DB_PATH = os.path.expanduser("~/.claude/hooks.db")

# Events whose payload carries a transcript we mine for per-message token usage.
# Mining is idempotent (dedup by message uuid), so we only bother on turn ends.
TOKEN_EVENTS = {"Stop", "SubagentStop", "PreCompact", "SessionEnd"}

# Drop hook-call rows older than this on startup so the DB doesn't grow forever.
RETENTION_DAYS = 60


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hook_calls (
                id         INTEGER PRIMARY KEY,
                ts         REAL NOT NULL,
                event      TEXT NOT NULL,
                session_id TEXT,
                cwd        TEXT,
                tool_name  TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hook_calls_ts ON hook_calls(ts)")
        # One row per assistant message; uuid PK makes re-mining a transcript a no-op.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                uuid                        TEXT PRIMARY KEY,
                ts                          REAL NOT NULL,
                session_id                  TEXT,
                model                       TEXT,
                input_tokens                INTEGER DEFAULT 0,
                output_tokens               INTEGER DEFAULT 0,
                cache_read_input_tokens     INTEGER DEFAULT 0,
                cache_creation_input_tokens INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(ts)")
        # Add `model` to token_usage created before this column existed.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(token_usage)")}
        if "model" not in cols:
            conn.execute("ALTER TABLE token_usage ADD COLUMN model TEXT")
        cutoff = time.time() - RETENTION_DAYS * 86400
        conn.execute("DELETE FROM hook_calls WHERE ts < ?", (cutoff,))


def _iso_to_epoch(s):
    try:
        s = s.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def mine_tokens(conn, transcript_path, session_id):
    """Upsert per-message token usage from a transcript JSONL file.

    Dedup is by the assistant message's `uuid` (INSERT OR IGNORE), so calling
    this repeatedly on a growing transcript never double-counts.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0
    rows = []
    try:
        with open(transcript_path, "rb") as fh:
            for line in fh:
                if b'"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") != "assistant":
                    continue
                uuid = d.get("uuid")
                msg = d.get("message") or {}
                usage = msg.get("usage") or {}
                if not uuid or not usage:
                    continue
                ts = _iso_to_epoch(d.get("timestamp") or "") or time.time()
                rows.append((
                    uuid, ts, d.get("sessionId") or session_id, msg.get("model"),
                    usage.get("input_tokens") or 0,
                    usage.get("output_tokens") or 0,
                    usage.get("cache_read_input_tokens") or 0,
                    usage.get("cache_creation_input_tokens") or 0,
                ))
    except Exception:
        return 0
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO token_usage "
            "(uuid, ts, session_id, model, input_tokens, output_tokens, "
            " cache_read_input_tokens, cache_creation_input_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def record_call(event, payload):
    """Log one hook call (and mine tokens on turn-ending events).

    Best-effort: any DB error is swallowed so logging never breaks sound/notify.
    """
    payload = payload or {}
    try:
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO hook_calls (ts, event, session_id, cwd, tool_name) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), event, payload.get("session_id"),
                 payload.get("cwd"), payload.get("tool_name")))
            if event in TOKEN_EVENTS:
                mine_tokens(conn, payload.get("transcript_path"),
                            payload.get("session_id"))
    except Exception as e:
        log(f"  \033[31mdb error\033[0m {event}: {e}")


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


def _is_managed_entry(entry, event):
    """True if `entry` is one --hooks installed for `event`.

    Identified by a command hook that runs `.../hook.sh <event>`, regardless of
    the checkout path. This lets a re-run replace our own entry in place
    (idempotent, and picks up a moved checkout) while leaving every other hook
    registered on the event untouched.
    """
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks", []):
        if not isinstance(h, dict) or h.get("type") != "command":
            continue
        parts = (h.get("command") or "").split()
        if len(parts) >= 2 and parts[-1] == event and os.path.basename(parts[-2]) == "hook.sh":
            return True
    return False


def install_hooks(settings_path):
    """Wire every Claude hook event to hook.sh in the Claude settings file.

    Merges into the existing settings without clobbering anything else: other
    top-level keys, hook events we don't list, and other hooks registered on an
    event we do list (e.g. a user's own SessionStart hook) are all preserved.
    Only our own previously-installed hook.sh entry for each event is replaced,
    so re-running is idempotent. A symlinked settings file (e.g. into a dotfiles
    repo) is written through, keeping the link intact.
    """
    settings_path = os.path.abspath(os.path.expanduser(settings_path))
    hook_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.sh")

    settings = {}
    if os.path.isfile(settings_path):
        with open(settings_path) as f:
            try:
                settings = json.load(f)
            except json.JSONDecodeError as e:
                print(f"error: {settings_path} is not valid JSON: {e}", file=sys.stderr)
                sys.exit(1)  # abort before overwriting the user's settings

    hooks_cfg = settings.setdefault("hooks", {})
    for name in HOOK_EVENTS:
        event_hooks = hooks_cfg.setdefault(name, [])
        # Drop only our own prior entry; keep every other hook on this event.
        event_hooks[:] = [e for e in event_hooks if not _is_managed_entry(e, name)]
        event_hooks.append({
            "matcher": "*",
            "hooks": [{
                "type": "command",
                "command": f"{hook_sh} {name}",
                "async": True,
            }],
        })

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"configured {len(HOOK_EVENTS)} hooks in {settings_path}")


def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"\033[90m{ts}\033[0m {msg}")


def vlog(msg):
    """Log only at the "verbose" level — for hooks that take no action."""
    if LOG_LEVEL == "verbose":
        log(msg)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/play":
            self.send_response(404)
            self.end_headers()
            return
        event = parse_qs(parsed.query).get("event", [None])[0]
        self._handle(event, None)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/play":
            self.send_response(404)
            self.end_headers()
            return
        event = parse_qs(parsed.query).get("event", [None])[0]
        payload = None
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 0:
                payload = json.loads(self.rfile.read(length))
        except Exception:
            payload = None
        self._handle(event, payload)

    def _handle(self, event, payload):
        if not event:
            vlog(f"  \033[33mignored\033[0m  no event specified")
            self.send_response(204)
            self.end_headers()
            return

        record_call(event, payload)  # log to SQLite (best-effort)

        sound_files = sounds.get(event)
        message = notifications.get(event)

        if not sound_files and not message:
            if event in sounds or event in notifications:
                vlog(f"  \033[90msilenced\033[0m {event}")
            else:
                vlog(f"  \033[33mignored\033[0m  {event} (unmapped)")
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
                    actions.append((False, f"\033[32mplay\033[0m     {event} -> {os.path.basename(path)}"))
                except FileNotFoundError:
                    # Don't let a missing mpv suppress the notification below.
                    mpv_missing = True
                    actions.append((False, f"\033[31merror\033[0m    {event}: mpv not installed"))
            else:
                actions.append((True, f"\033[90mthrottle\033[0m {event} (audio)"))

        if message:
            if now - _last_notify >= RATE_LIMIT:
                _last_notify = now
                subprocess.Popen(
                    ["notify-send", "Claude Code", message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                actions.append((False, f"\033[34mnotify\033[0m   {event} -> {message}"))
            else:
                actions.append((True, f"\033[90mthrottle\033[0m {event} (notify)"))

        # verbose=True entries (throttles) only show at the "verbose" level.
        for verbose, action in actions:
            (vlog if verbose else log)(f"  {action}")

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
    global LOG_LEVEL
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
        "--log-level",
        choices=LOG_LEVELS,
        default=LOG_LEVEL,
        help="Console verbosity. 'default' logs only real actions "
             "(play/notify/error); 'verbose' also logs no-action hooks "
             "(silenced/unmapped/throttled). Default from "
             "CLAUDE_HOOKS_LOG_LEVEL, else 'default'.",
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

    # mpv/notify-send are fire-and-forget: we Popen them and never wait().
    # Without this, each exited child lingers as a zombie (<defunct>) because
    # the long-running server never reaps it. SIG_IGN tells the kernel to
    # auto-reap, so no zombies accumulate. Safe here only because nothing in
    # this server ever calls .wait()/.poll() on a child.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    LOG_LEVEL = args.log_level
    print(f"log level: {LOG_LEVEL}")

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

    init_db()
    print(f"hook log: {DB_PATH}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
