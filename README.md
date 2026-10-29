# Relay CRM

A customer workspace that connects accounts, contacts, opportunities and agreement handoffs. Track a deal's next action, understand the pipeline and retain the decisions behind each change.

## Run

Python 3.10 or later; no packages to install.

```powershell
python server.py --seed
```

Open **http://127.0.0.1:8787**. The `--seed` option adds synthetic customer records only when the database is empty. To use a separate workspace:

```powershell
python server.py --db another-workspace.sqlite3 --seed --port 8788
```

## Use the workspace

1. Add an account and an associated contact from Accounts.
2. Create an opportunity with a value, next action and follow-up date.
3. Move it through Lead, Qualified and Proposal as the conversation progresses.
4. Prepare an agreement for its current terms. In the synthetic workspace, simulate completion to exercise the handoff.
5. Close the opportunity as Won. A completed agreement matching the current version is required.
6. Review pipeline totals, account coverage and overdue follow-ups, or export the current pipeline to CSV.

Changes to the opportunity invalidate an earlier agreement version. Lost opportunities can reopen as Lead; won opportunities remain closed. Activity notes remain available after closure.

The agreement handoff records a local workflow state. It is not an electronic-signature service and does not sign or send contracts. All included examples are synthetic. The server listens on loopback for local use; it is not an authenticated public CRM deployment.

## How the data stays consistent

- Foreign keys connect customer records; a primary contact must belong to the opportunity's account.
- Email and account-domain uniqueness are case insensitive.
- Every write requires an idempotency key. Retries return the stored result; reuse with different content is rejected.
- Opportunity versions prevent one editor from silently overwriting another editor's changes.
- The business change, activity record and request result commit in one transaction.
- Agreement snapshots bind the account, contact, title, monetary value and opportunity version to a SHA-256 digest.
- Currency is stored in integer cents. Forecast weights are illustrative: Lead 10%, Qualified 40%, Proposal 70%; each opportunity is rounded to cents and closed deals are excluded.
- CSV exports neutralize leading spreadsheet-formula characters in text fields.

## Verify

```powershell
python -m unittest -v test_engine
```

The checks exercise persistence, concurrency, retries, rollback, invalid transitions, agreement mismatches, reporting and HTTP boundaries. See [the API and design notes](DESIGN.md) for request examples and the data model.
