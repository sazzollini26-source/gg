"""Generate reviewable OS schedules. Generating a file never installs a job."""

from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path

from .briefing import atomic_text


def generate_schedule(store, platform: str) -> dict:
    config = store.config()
    hour, minute = map(int, config["brief_time"].split(":"))
    root = Path(__file__).resolve().parent.parent
    folder = store.home / "schedules"
    logs = store.home / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "daydesk", "--home", str(store.home), "run"]
    if platform == "macos":
        path = folder / "daydesk.plist"
        payload = {"Label": "com.daydesk.daily", "ProgramArguments": command,
                   "WorkingDirectory": str(root), "RunAtLoad": True,
                   "StartCalendarInterval": {"Hour": hour, "Minute": minute},
                   "StandardOutPath": str(logs / "daily.log"),
                   "StandardErrorPath": str(logs / "daily-error.log")}
        atomic_text(path, plistlib.dumps(payload).decode())
        instructions = [
            "mkdir -p ~/Library/LaunchAgents",
            f"cp {shlex.quote(str(path))} ~/Library/LaunchAgents/com.daydesk.daily.plist",
            'launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.daydesk.daily.plist',
            'To stop: launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.daydesk.daily.plist',
        ]
    elif platform == "linux":
        path = folder / "daydesk.cron"
        shell_command = f"cd {shlex.quote(str(root))} && {shlex.join(command)} >> {shlex.quote(str(logs / 'daily.log'))} 2>&1"
        # Cron gives unescaped percent signs a special meaning, even inside shell quotes.
        shell_command = shell_command.replace("%", r"\%")
        atomic_text(path, f"# Daydesk daily run; computer timezone must match {config['timezone']}.\n{minute} {hour} * * * {shell_command}\n")
        instructions = ["Run crontab -e, then copy the non-comment line from this file into it.",
                        "Keep existing cron entries. To stop, remove only the Daydesk line."]
    elif platform == "windows":
        path = folder / "daydesk.cmd"
        # Always quote arguments: list2cmdline leaves '&' unquoted when a path has no spaces.
        # Windows paths cannot contain double quotes; refuse such paths rather than rewriting them.
        if any('"' in argument for argument in [*command, str(root)]):
            raise ValueError("Windows scheduler paths cannot contain double quotes")
        windows_command = " ".join('"' + argument.replace("%", "%%") + '"' for argument in command)
        workdir = str(root).replace("%", "%%")
        atomic_text(path, f'@echo off\nsetlocal DisableDelayedExpansion\ncd /d "{workdir}"\n{windows_command}\n')
        instructions = [
            "Open Task Scheduler > Create Basic Task > name it Daydesk > Daily.",
            f"Set the start time to {config['brief_time']} in the computer's timezone.",
            f"Action: Start a program. Program/script: {path}",
            "Enable 'Run task as soon as possible after a scheduled start is missed'.",
            "To stop, disable or delete only the Daydesk task in Task Scheduler.",
        ]
    else:
        raise ValueError("Platform must be macos, linux, or windows")
    return {"path": str(path), "installed": False,
            "note": f"Daily at {config['brief_time']} in your computer's timezone. Match it to {config['timezone']}; keep Python and this folder in place.",
            "instructions": instructions}
