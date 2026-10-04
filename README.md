# Daydesk

A personal daily assistant written in Python. Keep deadlines, events, work hours, expenses, and reusable checklists in one local dashboard. Daydesk turns the information you enter into a daily brief and makes a backup when the daily routine runs.

The included checklists cover everyday planning, event preparation, activities, and weekly reviews. Edit these starter templates to match your responsibilities.

**Daydesk automates tasks you enter. It does not infer your schedule or access arbitrary accounts.** Email, calendar accounts, and AI services are not connected. It does not send messages or submit forms.

## Start here

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/).
2. Download the project ZIP and extract it, or clone this repository. Open a terminal inside the project folder, where `README.md` and `daydesk/` are visible.
3. Run:

```bash
python3 -m daydesk init
python3 -m daydesk serve --open
```

On Windows, use `py` in place of `python3` if that is your Python launcher. For example: `py -m daydesk init`. See [the automation guide](docs/AUTOMATION.md) for timezone setup if your installation reports missing timezone data.

The dashboard opens at **http://127.0.0.1:8765**. Leave its terminal running while you use it; press `Ctrl+C` to stop the server. The app runs locally on your computer.

Daydesk starts with the name `You`, timezone `UTC`, and a daily routine time of `07:00`. Before entering dates, set your own timezone in Settings or with `python3 -m daydesk config --timezone YOUR_IANA_TIMEZONE` (for example, `Europe/London`). Task categories start as Work, Home, Projects, and Personal.

Your personal records are stored separately from the repository in `~/.daydesk` by default. Publishing the code to GitHub does not upload those records unless you deliberately copy them into the repository.

## What you can use it for

| Need | Daydesk helps you |
| --- | --- |
| Plan your day | Read a daily brief with entered tasks and events |
| Track tasks | Add deadlines, priorities, categories, and repeating tasks |
| Prepare for work | Apply a checklist to create tasks when you need them |
| Record your hours | Log work sessions, hourly rates, and miles |
| Keep expenses readable | Record an amount, category, and description |
| Preserve your records | Run backups and export your data |
| Repeat the daily routine | Use a local scheduler or the foreground watcher |

Work totals are estimates based on the hours and rate you enter. Miles are a record, not a promise of mileage reimbursement.

## Everyday commands

Run these inside the repository folder. Entries below are **synthetic examples** that you can replace with your own plans.

```bash
# Set preferences.
python3 -m daydesk config --name Alex --timezone UTC --brief-time 07:00

# Add and review tasks.
python3 -m daydesk task add 'Review project notes' --category Projects --due '2026-10-04T17:00' --priority 1 --repeat weekly
python3 -m daydesk task list

# Replace ID with the task ID shown in the list.
python3 -m daydesk task done ID

# Add an event using local date and time.
python3 -m daydesk event add 'Example work shift' --start '2026-10-04T08:00' --end '2026-10-04T12:00' --location 'Example venue'

# Record an expense and a completed work session.
python3 -m daydesk expense add 12.50 --category Food --description 'Example lunch'
python3 -m daydesk work add --start '2026-10-03T09:00' --end '2026-10-03T12:00' --role 'Example shift' --rate 30 --miles 12

# View checklist names, then apply one when needed.
python3 -m daydesk checklist list
python3 -m daydesk checklist apply 'Event setup'

# Build a brief, run the daily routine, and preserve records.
python3 -m daydesk brief
python3 -m daydesk run
python3 -m daydesk backup
python3 -m daydesk export
```

Use `python3 -m daydesk --help` for available commands and `python3 -m daydesk task add --help` for command options.

Priority `1` is the highest priority. A repeating task creates its next occurrence when you mark it done. `backup` prints the saved SQLite file path; `export` prints the paths of the exported CSV files. Amount columns ending in `_cents` store whole cents: `1250` means `$12.50`.

Dates without an explicit UTC offset use your configured timezone. Set your timezone before adding records. Use full dates such as `2026-10-04T17:00` so a deadline cannot be confused with another day.

## Correct a record

Replace `ID` with the record's ID from its list command. You can update an unfinished task without recreating it:

```bash
python3 -m daydesk task edit ID --due '2026-10-05T17:00'
python3 -m daydesk task edit ID --title 'Review updated project notes' --priority 1
python3 -m daydesk task edit ID --repeat none --clear-due
```

Task edits also support `--category`, `--notes`, and `--repeat none|daily|weekly`. Completed tasks cannot be edited. An overnight event or work log needs the following day's date on its end time.

To remove a record, use `task delete ID`, `event delete ID`, `expense delete ID`, or `work delete ID` after `python3 -m daydesk`. **Deletion is permanent.** The dashboard asks you to confirm; the CLI deletes immediately. To correct an event, expense, or work entry, delete the incorrect record and add the corrected one.

```bash
# Replace ID with the incorrect expense's ID, then enter its replacement.
python3 -m daydesk expense delete ID
python3 -m daydesk expense add 10.50 --category Food --description 'Corrected example lunch'
```

## Make the daily routine automatic

`run` writes your daily Markdown brief and creates a SQLite backup once per local day. Re-running it updates the brief. `watch` runs the routine when it starts, then keeps a foreground process running to check reminders and the configured daily time:

```bash
python3 -m daydesk watch
```

To prepare a computer scheduler configuration, choose your platform:

```bash
python3 -m daydesk schedule --platform macos
python3 -m daydesk schedule --platform linux
python3 -m daydesk schedule --platform windows
```

These commands produce local scheduler files and installation instructions. They **do not enable a scheduled job**. Follow [docs/AUTOMATION.md](docs/AUTOMATION.md) to review and install the job on your computer. The machine must be available to run it, and its timezone should match your Daydesk timezone.

Browser notifications are optional and require your permission and the dashboard page to stay open. There is no server or mobile push service.

## Data and backup

Use the global `--home` option **before the subcommand** to choose a different data folder:

```bash
python3 -m daydesk --home './my-daydesk-data' init
python3 -m daydesk --home './my-daydesk-data' serve --open
```

You can also set the `DAYDESK_HOME` environment variable. Use the same home location for the dashboard, CLI commands, and scheduled routine so they read the same records.

Keep data folders, exports, backups, and credentials out of GitHub. A private code repository is a sensible starting point; see [docs/GITHUB.md](docs/GITHUB.md).

## Development

The core application uses Python's standard library. There are no runtime dependencies on macOS or Linux systems that provide timezone data; some Windows or minimal installations need the `tzdata` package.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q daydesk
```

To add an integration or another workflow, see [docs/EXTENDING.md](docs/EXTENDING.md). Start with one specific input and output, such as importing a calendar export or summarizing a CSV, and keep account credentials outside the repository.
