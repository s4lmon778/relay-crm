# Data and API design

## Model

An account has many contacts and opportunities. An opportunity identifies one contact from the same account. Agreements and activities belong to an opportunity. Each agreement stores an immutable JSON snapshot and its digest. The application never updates an issued snapshot; commercial changes require a new version. Activities are appended through the API.

SQLite enforces foreign keys, unique domains/emails and nonnegative monetary values. Each write takes an immediate transaction before reading current state, checking the request key and applying the mutation. On failure, the change, activity and request result roll back together. Read-side reports use a transaction so their component queries share a snapshot.

## HTTP interface

All writes use `Content-Type: application/json` and an `Idempotency-Key` header. Request bodies are limited to 16 KiB. The API returns `422` for invalid data or transitions, `404` for unknown records, and `409` for conflicting keys, stale versions or agreement mismatches. Cross-origin requests and unrecognized hosts are rejected. No CORS access is enabled.

| Route | Body / response |
| --- | --- |
| `GET /api/workspace` | Accounts, contacts, opportunities, agreements, activities |
| `GET /api/report` | Stage totals, account coverage, open/weighted/won values and overdue count |
| `GET /api/export.csv` | Spreadsheet-safe pipeline export |
| `POST /api/accounts` | `name`, `domain` |
| `POST /api/contacts` | `account_id`, `name`, `email` |
| `POST /api/opportunities` | `account_id`, `contact_id`, `title`, `amount_cents`, `next_action`, `due_date` |
| `POST /api/update` | `id`, `version`, optional `stage`, `amount_cents`, `next_action`, `due_date` |
| `POST /api/note` | Opportunity `id`, `detail` |
| `POST /api/issue` | Opportunity `id`, `version` |
| `POST /api/complete` | Opportunity `id`, `version`, `agreement_id`, snapshot `digest` |

Example request body when advancing an opportunity:

```json
{"id": 1, "version": 1, "stage": "Qualified"}
```

The response contains the new version. A later request must use that version, not the one from an earlier screen. Replaying the original request with its original key returns the original result without applying another update.

## Scope and extension points

This is a standalone Python/SQLite/JavaScript implementation of CRM concepts. It does not connect to Salesforce, AWS, Docusign or an email service. It does not generate legally binding signatures. A production deployment would require identity/authorization, tenant isolation, lifecycle policies, deployment operations and a real agreement-provider integration. The current completion endpoint deliberately exercises a synthetic local workflow.

Account/contact concepts were checked against [Salesforce's primary CRM explanation](https://trailhead.salesforce.com/content/learn/modules/accounts_contacts_lightning_experience/accounts-and-contacts-lightning). Transaction behavior follows [SQLite's transaction documentation](https://www.sqlite.org/lang_transaction.html). Reading those references does not represent Salesforce training completion or a credential.
