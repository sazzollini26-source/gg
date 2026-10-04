# Add another automation

Daydesk is a starting point for a personal assistant you control. Its current inputs are the records you enter through the CLI and dashboard. New integrations need explicit setup; having this repository does not grant access to an account or service.

Choose one concrete workflow first. Define where the input comes from, what should be produced, how often it should run, and what requires your review.

## Useful next steps

| Workflow | First version | What is needed |
| --- | --- | --- |
| Project deadlines | Import a file exported from a task tool | A sample file and a rule for duplicates |
| Calendar overview | Read an exported calendar file | Timezone handling and clear ownership of imported events |
| News tracking | Turn an authorized news feed into a review queue | Feed sources, source links, and duplicate detection |
| Work records | Import a work-hours CSV | Column definitions and validation of times and rates |
| Event preparation | Add a checklist for one event type | Your approved responsibilities and checklist wording |
| AI summary | Summarize records you choose to share | A provider, credentials, cost limits, and a privacy decision |

These are potential extensions. They are not included connections or services.

## A practical implementation pattern

1. **Read.** Start with a local file or an official, authorized API. Validate dates, identifiers, and required fields.
2. **Preview.** Show what will be added or changed before the first real import.
3. **Store.** Keep the original source identifier so a second import does not create duplicates.
4. **Report.** Tell the user which records succeeded and which need attention. Do not quietly discard failures.
5. **Schedule.** Add the integration to the daily routine only after it behaves correctly on real sample data.

Separate data collection from actions that send, publish, purchase, or submit anything. Add a clear user review step before enabling those actions.

## Protect personal records

- Use environment variables or a dedicated local secrets file for API credentials. Never commit them.
- Request only the access an integration needs, such as read-only calendar access for an overview.
- Keep source links on imported information so summaries can be checked.
- Avoid putting sensitive personal data into logs or test fixtures.
- Test against a temporary `--home` folder rather than your live records.

For a clean development data folder:

```bash
python3 -m daydesk --home './dev-data' init
python3 -m daydesk --home './dev-data' serve --open
```

Make sure the development data directory is excluded from Git before adding anything personal.

## Check a change

Run the included tests and a syntax check:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q daydesk
```

For an importer, meaningful checks include repeated imports, missing fields, malformed dates, different timezones, and a partially failed request. For a scheduler, verify that the same daily routine does not create duplicate records.

Keep the README and CLI help accurate when you add a command. Describe which accounts are connected, where data goes, what the routine changes, and what the user must configure.
