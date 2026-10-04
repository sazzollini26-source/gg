# Running Daydesk every day

Daydesk runs on your computer. A scheduled routine can generate your brief and backup without opening the dashboard, but it needs a working Python installation and access to your Daydesk data folder.

This repository does not create a ChatGPT scheduled task. It also does not keep running after the computer is turned off.

## Set your preferences first

The default timezone is `UTC`. Replace it below with your own IANA timezone, such as `Europe/London`, before entering local dates. `Alex` is a synthetic example name.

```bash
python3 -m daydesk config --name Alex --timezone UTC --brief-time 07:00
python3 -m daydesk run
```

The first command sets the preferred daily time. The second checks that the routine can run successfully before you schedule it.

`run` writes a Markdown daily brief and makes one SQLite backup per local day. Running it again on the same day refreshes the brief without creating another daily backup. Manual `backup` is available when you need a separate checkpoint.

Checklists create tasks when you explicitly apply them. They are not automatically converted into obligations each morning.

## Option 1: Keep a watcher open

```bash
python3 -m daydesk watch
```

Leave the terminal running. The watcher runs the daily routine when it starts, prints reminders in the terminal, and checks the configured brief time for the next daily run. Stop it with `Ctrl+C`.

This is convenient for trying the app. Closing the terminal, shutting down Python, or turning off the computer stops it.

## Option 2: Install a computer scheduler job

Run the command for your operating system:

| System | Command | Generated file |
| --- | --- | --- |
| macOS | `python3 -m daydesk schedule --platform macos` | `~/.daydesk/schedules/daydesk.plist` for launchd |
| Linux | `python3 -m daydesk schedule --platform linux` | `~/.daydesk/schedules/daydesk.cron` for cron |
| Windows | `py -m daydesk schedule --platform windows` | `~/.daydesk/schedules/daydesk.cmd` for Task Scheduler |

The command writes the file and prints installation instructions. A custom `--home` changes its location. Review the generated paths and time before following the instructions. **Generating a schedule does not install or enable it.**

Keep the repository in a stable folder. The generated job needs the same Python installation, source directory, and data directory used when you prepared it. Moving the repository or replacing Python can require regenerating the job.

The generated macOS, Linux, and Windows jobs interpret their scheduled hour in the **computer's local timezone**. Daydesk's configured timezone controls dates in the brief, but does not change the computer scheduler's timezone. Set your computer and Daydesk to the same timezone, such as `UTC`, before enabling the job. If they differ, adjust the system schedule appropriately and account for daylight saving time. The foreground watcher uses Daydesk's configured timezone.

Computer schedulers differ in how they handle sleep and missed runs. Keep the machine awake at the selected time, or configure wake and missed-run behavior in your operating system where supported. Run `python3 -m daydesk run` manually after a missed day.

## Timezone data

Daydesk uses named IANA timezones, such as `UTC`. Regional zones, such as `Europe/London`, also account for daylight saving changes. Python relies on the operating system's timezone database or the optional `tzdata` package.

If your installation reports missing timezone data, install it into the same Python environment used to run Daydesk:

```bash
python3 -m pip install tzdata
```

On Windows:

```powershell
py -m pip install tzdata
```

Then rerun the configuration command. Be sure the interpreter used by the scheduled job can access the same timezone data.

## Reminders and useful boundaries

- Browser notifications are optional and require your permission and the dashboard page to remain open. There is no server or mobile push service.
- An event in Daydesk does not appear in external calendar accounts automatically.
- A repeating task creates its next occurrence when you mark it done. Missed occurrences are skipped rather than filled into your task list; verify that this matches your real deadline pattern.
- Use `task edit ID --due 'YYYY-MM-DDTHH:MM'` to reschedule an unfinished task. Completed tasks cannot be edited. Record deletion is permanent; the CLI deletes immediately and the dashboard asks for confirmation.
- Work hours, rates, and miles come from your records. Daydesk does not verify payroll or employer reimbursement rules.
- Backups in the same computer can help recover accidental changes. Copy important backups to another safe location for protection against device loss.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `No module named daydesk` | Open the terminal in the repository root, or check the job's working directory. |
| Dashboard does not load | Start `serve`, keep its terminal open, and visit `http://127.0.0.1:8765`. |
| Commands show different records | Check `DAYDESK_HOME` and the global `--home` setting in every command. |
| Scheduled job does not run | Confirm it was installed, the machine was available, and all generated paths still exist. |
| Job runs at the wrong hour | Check the operating system's timezone and scheduler settings as well as Daydesk's configuration. |
| Missing timezone error | Install `tzdata` with the same Python interpreter used by Daydesk. |

Use `python3 -m daydesk --help` or a subcommand's `--help` to check the supported options.
