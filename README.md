# Business Suite

An all-in-one, self-hosted business management platform modeled on how Odoo works: one app grid, one
database, and apps that share their data. A confirmed quotation creates a delivery and an invoice, a
paid invoice lands in the ledger, a receipt updates stock. This is original code. It is not affiliated
with Odoo S.A. and contains no Odoo code or branding.

## Apps

| Area | Apps |
|---|---|
| Finance | Accounting (double-entry, invoices, bills, credit notes, payments, reports), Subscriptions, Expenses, Payroll |
| Sales | CRM, Sales, Point of Sale, Email Marketing, Events |
| Supply chain | Inventory, Purchase, Manufacturing, Maintenance, Fleet |
| People | Employees, Recruitment, Time Off, Attendances, Timesheets, My Payslips |
| Services | Project, Helpdesk |
| Productivity | Dashboards, Discuss, Calendar, To-do, Knowledge, Documents, Contacts, Settings |

Every app gets the same tools: list, kanban, form, calendar, graph and pivot views, search with filters
and group-by, CSV import and export, a record history with notes, activities and attachments.

### Compared with Odoo

Odoo has decades of work behind it, and this covers its core workflows rather than all of it. **Not
included:** website builder and eCommerce, multi-company, multi-currency, lot and serial tracking,
perpetual stock valuation (bills go straight to expense or cost of goods), bank statement import and
reconciliation, e-signatures, a visual view editor, and third-party module installation.

Payroll is a flat-rate withholding on a monthly wage, **not** a statutory tax calculation. Check any
figures you file with an accountant.

## Run it

Needs Docker with Compose. Everything is built in containers.

```bash
cp .env.example .env      # then set DB_PASSWORD and ADMIN_PASSWORD (12+ characters)
docker network create tunnel-net
docker compose up -d --build
```

Open http://localhost:8096 and sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Create users under
Settings > Users, give them access rights, and use Set Password. To start without the sample company,
set `SEED_DEMO_DATA=false` **before the first start**.

### Deploying behind Cloudflare

The deploy script `pipeline-run.sh` is git-ignored, so it exists only on the server. It follows the same
steps as the other projects on this server: it fills in missing secrets in `.env`, pulls the code, removes
**all** containers on the host, starts the `cloudflared-tunnel` container, then this stack. It needs
`TUNNEL_TOKEN` set globally or in `.env`, and the tunnel's public hostname must point to
`http://rixus-frontend:80`.

## How it is built

```
frontend/   React + TypeScript, served by unprivileged nginx, which proxies /api
backend/    Python + FastAPI
  app/orm.py        model registry, schema sync, access checks, domains to SQL
  app/views.py      helpers that declare views
  app/modules/      one file per business area: models, rules, menus
  app/seed.py       required defaults and the optional demo company
  tests/            integration tests against real Postgres
docker-compose.yml  db, api, web
```

Like Odoo, the whole system is driven by model definitions. A model in `app/modules/` declares its fields,
access rights, workflow actions, views and menus. From that one declaration come the database table, the
REST API and the screens. On startup the schema sync creates missing tables and columns. It never drops or
changes existing ones: renames and type changes need a manual migration.

### Adding an app

Add a file to `backend/app/modules/`, import it in `modules/__init__.py`, and define models and an `app(...)`
entry. `validate_registry()` stops startup with a clear error if a view or relation refers to something
that does not exist.

### Tests

The tests need a Postgres database they can write to, so use a throwaway one:

```bash
docker network create suite-test
docker run -d --rm --name suite-test-db --network suite-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=suite --tmpfs /var/lib/postgresql/data postgres:16.6-alpine
docker build -t suite-api backend
docker run --rm --network suite-test -e DATABASE_URL=postgresql://postgres:test@suite-test-db:5432/suite -e PYTHONPATH=/srv -w /srv/tests suite-api python -m pytest -q -p no:cacheprovider
docker rm -f suite-test-db
```

Add `-e SEED_DEMO_DATA=true` to also check that the demo company's books balance.

## Security

Secrets live only in the git-ignored `.env`. Sign-in uses Argon2 password hashes, revocable server-side
sessions in an HttpOnly cookie, and throttling. Access is enforced on the server for every request, by app
group, by record (employees see only their own expenses, leave and payslips) and by field (private employee
data). Only the web container is reachable from outside, and only the API can reach the database.
