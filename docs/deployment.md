# PLT deployment

How the Pesticide Litigation Tracker is put on a server, and the reasoning behind the shape
it takes there. Companion documents: [`README.md`](../README.md) (what the app is),
[`docs/architecture.md`](architecture.md) (the integration contract),
[`CONTRIBUTING.md`](../CONTRIBUTING.md) (running it locally).

---

## 1. What this deployment is for

This describes **one virtual machine** serving the tracker to people who need a URL rather
than a checkout: the Law group, collaborating scholars, and whoever is curating the keyword
lists. It is a shared, always-on instance of the development work — not the tracker's final
home.

**The final home is likely a WUR-managed machine or VM.** That is the single most important
fact about this document, and it sets one rule that everything below obeys:

> Nothing in this deployment may depend on who the hosting provider is.

Ubuntu, systemd, PostgreSQL, Caddy and a git checkout exist on any Linux VM a university IT
department will hand you. A Hetzner box is a convenient place to rehearse; the runbook is
written so that moving it is a matter of provisioning a different machine and re-running
§5–§10 on it. [§13](#13-moving-to-a-university-vm) lists exactly what changes.

Two scoping decisions already taken:

| Decision | Consequence |
| --- | --- |
| The site runs on a **project-owned, non-WUR domain** | TLS is Let's Encrypt via Caddy, and DNS is ours to change. A move to a `wur.nl` domain means going through WUR IT for both, and re-scoping §13. |
| Mail sends through **a mailbox on that same domain** | The provider gives us ordinary SMTP submission credentials, so no WUR mail infrastructure is needed. See §10. |

Access is **SSH, one operator, key-only** to begin with. §5.4 sets it up so that adding
people later is adding a key and a group member, not re-thinking access.

---

## 2. Topology

One VM, one origin, four long-lived pieces:

| Piece | Listens on | Purpose |
| --- | --- | --- |
| **Caddy** | `:80`, `:443` (public) | TLS termination, serves the built SPA, reverse-proxies `/api` to Gunicorn |
| **Gunicorn** → `plt.app:create_app()` | `127.0.0.1:8000` | The Flask API |
| **PostgreSQL** | local socket | The published database |
| **systemd timers** | — | Weekly ingest, digest, subscriber purge (§9) |

Everything the browser touches arrives on **one origin**, `https://<domain>`. This is not a
stylistic choice: the frontend ships with `VITE_API_BASE_URL=/api` and the backend's
`PLT_CORS_ALLOWED_ORIGINS` exists as an escape hatch for a split deployment we do not have.
Keeping the SPA and the API on one origin means CORS never engages, the cookie flags
`create_app` already sets stay meaningful, and the configuration that runs in production is
the configuration developers run locally behind the Vite proxy.

Splitting the frontend onto a static host (Vercel, Netlify) would mean either proxying `/api`
back to this VM — a second vendor to reproduce what one origin gives for free — or going
cross-origin and maintaining an allowlist. Neither buys anything for a site this size.

---

## 3. The two corpora

The most important thing to understand before operating this system: **there are two bodies
of case law, they live in different places, and only one of them is on the server.**

### 3.1 The mirror — the full corpus

`plt mirror` writes every document a connector enumerates to `PLT_CORPUS_STORE_DIR`, verbatim,
one folder per case (architecture §9). It is the raw material: roughly 140 kB per EU case,
about 15 GB for CELEX sector 6 alone, and considerably more once member states are added.

**The mirror lives on the workstation, never on the server.** It exists so that a selection
experiment is repeatable — so two keyword lists can be scored against an identical corpus
rather than against whatever the endpoint happened to hold that day. That is a research
activity, not a serving activity. Renting server storage that grows into tens of gigabytes to
hold bytes the public site never reads would be paying rent on an archive.

### 3.2 The published database — the filtered corpus

What survives the filter chain: currently 4,339 cases, 5,622 documents, about 158 MB of full
text. In PostgreSQL, where large text is TOAST-compressed, expect roughly 250–400 MB restored.
Even at ten times the jurisdictions this stays in low single-digit gigabytes.

**This is the only corpus on the server**, and it is the only thing the API ever queries.

### 3.3 Weekly updates run on the server

The scheduled scan is incremental: it reads `ingest_checkpoint`, asks each source only for
what changed since, and appends. It is small, it needs no mirror, and architecture §7 already
fixes its semantics — a failed run must not advance the checkpoint.

So it belongs on the VM, as a systemd timer calling the same CLI the GitHub workflows call
(§9). Nothing about the code changes; only the thing holding the clock.

### 3.4 A methodology change is rebuilt on the workstation

Changing a keyword list is not an increment. It re-scores **the entire corpus**, which means
it needs the mirror, which means it happens where the mirror is.

The loop, run on the workstation against a local PostgreSQL matching the server's major
version:

```bash
# 1. Bring the mirror up to date first. It is incremental and checkpointed; skipping this
#    re-scores a corpus missing whatever the server ingested since the last mirror run.
plt mirror --every-jurisdiction

# 2. Rebuild the filtered database from disk. No court is asked for a single document.
plt ingest --every-jurisdiction --from-store

# 3. Dump only the regenerable tables (see §3.5 for why "only").
pg_dump --format=custom --no-owner --no-privileges \
        --table=case --table=case_document --table=citation --table=party \
        --table=keyword_match --table=court --table=topic --table=case_topic \
        --table=jurisdiction \
        plt_rebuild > plt-corpus-20260901.dump
```

Ship that file to the server and restore it (§3.6). The corpus never leaves the workstation;
only its filtered output crosses the network, compressed, in the low hundreds of megabytes.

### 3.5 What a republish must not destroy

"Wipe the database and load the new run" is the right instinct with one crucial qualification.
The live database holds two kinds of thing, and only one is regenerable:

| Regenerable from the mirror — replace freely | Operational state — must survive |
| --- | --- |
| `case`, `case_document`, `citation`, `party`, `keyword_match`, `court`, `topic`, `case_topic`, `jurisdiction` | `subscriber`, `case_review`, `case_review_decision`, `ingest_checkpoint`, `ingest_run` |

Three facts make this workable, and one makes it sharp:

- **`subscriber` has no foreign keys at all.** It detaches cleanly — leave the table alone and
  it is unaffected by anything the republish does.
- **Losing it would be unrecoverable.** The table holds the pseudonymised digests of addresses
  that have unsubscribed. Those digests *are* the suppression list. Drop them and a person who
  opted out becomes a stranger the site will happily re-subscribe — the same class of
  irreversible mistake that `.env.example` warns about for rotating
  `PLT_SUBSCRIPTION_ADDRESS_PEPPER`. Never restore over this table.
- **`ingest_checkpoint` keys on `jurisdiction_code`, not on case ids**, so it survives a
  rebuild untouched and correctly. Carry it across, or the next weekly run re-walks all of
  history.
- **`case_review.case_id` is a real foreign key to `case.id`, and case ids are autoincrement.**
  A rebuild renumbers every case, so review decisions either dangle or — worse — silently
  repoint at an unrelated case. This needs a decision before the first republish, not during
  one. Either re-key review rows on `(jurisdiction_code, source_id)` the way public URLs
  already are, or accept that a methodology rebuild resets the review queue and write that
  down. Doing neither is the bug.

**Public URLs are safe.** Case pages are addressed `/cases/<jurisdiction>/<source_id>` — ECLI
and CELEX — not by database id. A rebuild renumbers rows without breaking a single bookmark or
citation. For a database scholars cite, this is what makes routine republishing acceptable at
all.

### 3.6 Cutover

Restore into a staging schema, then flip. The rename is a metadata operation, so the site
changes over between two requests rather than serving half a corpus for a minute:

```bash
# On the server: load the dump into a schema nothing is reading yet.
pg_restore --no-owner --no-privileges --schema=public --dbname=plt_staging plt-corpus-20260901.dump
```

```sql
-- Then flip, in one transaction.
BEGIN;
ALTER SCHEMA public RENAME TO retired_20260901;
ALTER SCHEMA incoming RENAME TO public;
COMMIT;
```

Keep the retired schema until the new one has been exercised; it is the rollback. Then:

```bash
# Catch up whatever the sources published between the local rebuild and this cutover.
# The checkpoint that came across in the dump tells it exactly where to resume.
sudo -u plt /srv/plt/venv/bin/plt ingest --every-jurisdiction
```

That last step closes the gap between a rebuild made on Monday and a cutover done on Thursday,
using the same incremental machinery as the weekly run.

---

## 4. Making search work on the VM

**This is the one place where moving to PostgreSQL makes something worse, and it needs
attention before real users arrive.**

`repositories.py` builds a `%term%` pattern and runs `ILIKE` against `case.title`,
`case.abstract`, `case.source_id` and — through an `EXISTS` — `case_document.full_text`. The
leading wildcard means no B-tree index can help. Today that is a sequential scan over 158 MB
of text per query. On SQLite with 4,339 cases on a fast local disk you do not notice; on a
2-vCPU VM with several people searching you will.

### 4.1 Use `pg_trgm`, not `tsvector`

The obvious instinct is PostgreSQL full-text search. **Resist it**, for a reason specific to
this corpus rather than to performance:

Dutch compounds words. A `tsvector` with a Dutch stemmer indexes whole words, so a search for
`gewasbeschermingsmiddel` would not match `gewasbeschermingsmiddelenwet` — the statute whose
name contains it. The same applies to `bestrijdingsmiddelenwet`, to `spuitzone` compounds, and
to a great deal of German once that jurisdiction is onboarded. Substring matching is not a
naive placeholder here; it is the semantics this domain actually wants, and `repositories.py`
chose it deliberately.

A trigram GIN index accelerates exactly the `ILIKE '%term%'` queries already written, with no
change to the query, the schema, or the results. The behaviour stays identical to SQLite's;
only the speed changes.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY ix_case_title_trgm
    ON "case" USING gin (title gin_trgm_ops);
CREATE INDEX CONCURRENTLY ix_case_abstract_trgm
    ON "case" USING gin (abstract gin_trgm_ops);
CREATE INDEX CONCURRENTLY ix_case_document_full_text_trgm
    ON case_document USING gin (full_text gin_trgm_ops);
```

Add these as an Alembic revision guarded on the dialect, so the migration set stays portable
and SQLite development is untouched:

```python
def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    ...
```

### 4.2 What to expect

- **Index build is the expensive part.** Raise `maintenance_work_mem` (1–2 GB) for the build.
  Use `CONCURRENTLY` on a live database; note that Alembic runs migrations in a transaction by
  default and `CREATE INDEX CONCURRENTLY` cannot run inside one — so build these outside the
  migration on an already-serving box, or accept the lock during a maintenance window.
- **A trigram GIN index over full text is not small** — budget for it to approach the size of
  the text itself. On a corpus this size that is tens of megabytes; watch it as jurisdictions
  are added.
- **Queries shorter than three characters cannot use a trigram index** and fall back to a scan.
  `PLT_PAGE_SIZE_MAX` bounds what such a query returns, but not what it costs. If short queries
  turn out to be common, reject them at the API rather than serving them slowly.
- **`ILIKE` is not `LIKE`.** SQLite's `LIKE` is case-insensitive for ASCII only; PostgreSQL's
  `ILIKE` is fully Unicode-aware. Accented Dutch and, later, French or Spanish terms will match
  *more* in production than in development. That is the better behaviour, but it means a search
  test passing locally is not proof about the server. Worth a case in the suite.

### 4.3 Then re-check the sort

`_ordering()` implements `relevance` as a `CASE` over the same `ILIKE` patterns — title hit
above abstract hit above full-text hit — because, in its own words, the schema stays portable.
That stays correct with trigram indexes and needs no change. Revisit it only if the Law group
asks for real relevance ranking, which is a `tsvector` conversation and brings the compounding
problem back with it.

---

## 5. Provision the VM

### 5.1 The machine

Ubuntu 24.04 LTS. Two vCPU and 4 GB of RAM is genuinely enough for the API and a database of
this size; take 8 GB if you intend to build trigram indexes comfortably while serving. Disk is
dominated by the database and its backups, not the corpus — 40 GB is ample. Confirm current
tiers and pricing with the provider.

### 5.2 Users

```bash
adduser --system --group --home /srv/plt plt   # runs the service, owns nothing else
adduser <operator>                             # a human login
usermod -aG sudo <operator>
```

The application never runs as root and never logs in.

### 5.3 Base packages

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib \
               caddy git ufw nodejs npm unattended-upgrades
dpkg-reconfigure --priority=low unattended-upgrades
```

Ubuntu 24.04 ships Python 3.12 and PostgreSQL 16, both fine — the backend requires 3.11+.
Check `node --version` is 20 or later; if the distribution's is older, install Node 20 from
NodeSource, because `package.json` sets `"engines": { "node": ">=20" }`.

### 5.4 Network access

Public: HTTP and HTTPS only. Administrative access goes over Tailscale, so SSH never needs a
port open to the internet:

```bash
tailscale up --ssh
ufw default deny incoming
ufw allow 80,443/tcp
ufw allow in on tailscale0
ufw enable
```

In `/etc/ssh/sshd_config`: `PasswordAuthentication no`, `PermitRootLogin no`.

Adding a colleague later is then adding them to the tailnet and dropping a key in
`~/.ssh/authorized_keys` — no firewall or listener changes, which is the point of setting it
up this way while there is still only one operator.

---

## 6. PostgreSQL

```bash
sudo -u postgres createuser --pwprompt plt
sudo -u postgres createdb --owner=plt plt
sudo -u postgres psql -d plt -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm'
```

Leave it on the local socket; nothing outside this VM has any business reaching it. Keep the
major version aligned with the workstation's — `pg_restore` tolerates older-to-newer, and
lining them up removes a category of surprise from every republish.

---

## 7. The application

```bash
sudo -u plt -H bash
git clone -b dev https://github.com/IdseVal/PLT.git /srv/plt/app
python3 -m venv /srv/plt/venv
/srv/plt/venv/bin/pip install -c /srv/plt/app/backend/constraints.txt /srv/plt/app/backend
/srv/plt/venv/bin/pip install "psycopg[binary]" gunicorn
```

**Two dependencies are not in `pyproject.toml`, deliberately, and both are required here:**

- **`psycopg`** — the PostgreSQL driver. `pyproject.toml` declares no database driver at all,
  because development runs on SQLite, which is in the standard library. Without this,
  `PLT_DATABASE_URL=postgresql+psycopg://...` fails at startup with a driver error that reads
  like a configuration mistake. It is not; the driver is simply absent.
- **`gunicorn`** — a deployment concern, not an application one.

Pin both in whatever records this server's environment; they are as much a part of the
deployment as the code is.

Then apply migrations:

```bash
set -a; . /etc/plt/plt.env; set +a
cd /srv/plt/app/backend && /srv/plt/venv/bin/alembic upgrade head
```

### 7.1 Worker configuration, and one rule about it

Start with **one process and threads**:

```
/srv/plt/venv/bin/gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8000 "plt.app:create_app()"
```

That is not timidity. `PLT_RATE_LIMIT_STORAGE_URI=memory://` is per-process, so the moment a
second worker starts, `10 per hour` on the export endpoint quietly becomes ten per hour *per
worker*. One process keeps the configured limits true. Threads are safe: the session is opened
per request on `flask.g` and closed by a `teardown_appcontext` handler, so concurrent requests
never share one.

**When you do add workers, do not use `--preload`.** The engine is process-wide and cached;
`dispose_engine()` exists but nothing calls it on fork. Preloading would build the connection
pool in the master and hand inherited TCP connections to every worker — a classic and
unpleasant corruption. Gunicorn does not preload by default, so the safe path is to leave that
alone; if you ever need it, add a `post_fork` hook that calls `dispose_engine()`. At that point
also move the rate limiter to Redis and set `PLT_RATE_LIMIT_STORAGE_URI` accordingly.

---

## 8. Frontend and Caddy

Build on the box, from the same checkout the API runs from, so the SPA and the API cannot
drift apart:

```bash
cd /srv/plt/app/frontend
npm ci
npm run build          # typechecks, then writes dist/
```

`/etc/caddy/Caddyfile`:

```
<domain> {
    encode gzip zstd
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * /srv/plt/app/frontend/dist
        try_files {path} /index.html
        file_server
    }
}
```

`try_files {path} /index.html` is what makes deep links work. The SPA uses `BrowserRouter`, so
a visitor opening `/cases/NL/ECLI:NL:RBDHA:2024:1234` directly — which is exactly what a
citation in a paper looks like — must be served `index.html` rather than a 404. Test this
explicitly after every deploy; it is invisible until someone shares a link.

Caddy obtains and renews the certificate automatically once the domain's A record points here.

---

## 9. Scheduling

Architecture §7 fixes the semantics; systemd supplies the clock. Four units in
`/etc/systemd/system/`.

**`plt-api.service`**

```ini
[Unit]
Description=PLT API
After=network.target postgresql.service

[Service]
User=plt
WorkingDirectory=/srv/plt/app/backend
EnvironmentFile=/etc/plt/plt.env
ExecStart=/srv/plt/venv/bin/gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8000 "plt.app:create_app()"
Restart=always

[Install]
WantedBy=multi-user.target
```

**`plt-ingest.service`** — `ExecStart=/srv/plt/venv/bin/plt ingest --every-jurisdiction --strict`,
with `OnSuccess=plt-digest.service` in its `[Unit]` section. `--strict` exits 3 when a run
completed but documents failed, so `systemctl status` goes red instead of a silent partial
scan. `OnSuccess=` reproduces the contract the workflows encode: the digest announces the
cases *that scan just landed*, so it is triggered by the ingest's completion rather than by a
clock of its own, and a failed scan sends nothing.

**`plt-digest.service`** — `ExecStart=/srv/plt/venv/bin/plt digest`. No timer; it is triggered.

**`plt-purge.service`** — `ExecStart=/srv/plt/venv/bin/plt purge-subscribers`, weekly. Safe to
enable immediately: with no retention configured it does nothing and says so (§14).

Each `.service` gets a matching `.timer` with `Persistent=true`, so a scan missed while the
machine was down runs when it comes back rather than being skipped.

All four run the same code paths as `.github/workflows/weekly-ingest.yml` and
`weekly-digest.yml`. Decide which schedule is authoritative and disable the other — two
schedulers ingesting into different databases is a confusing afternoon.

---

## 10. Configuration and secrets

`/etc/plt/plt.env`, owned by root, mode `0600`, referenced by every unit through
`EnvironmentFile=`. Never in the checkout — `.gitignore` excludes `.env` and `.env.*` precisely
so this cannot be committed by reflex.

Every variable is documented in [`backend/.env.example`](../backend/.env.example). What
production specifically requires:

```bash
PLT_APP_ENV=production
PLT_DEBUG=false
PLT_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
PLT_SUBSCRIPTION_ADDRESS_PEPPER=<generate separately, never rotate>
PLT_DATABASE_URL=postgresql+psycopg://plt:<password>@localhost:5432/plt
PLT_SITE_BASE_URL=https://<domain>
PLT_CORS_ALLOWED_ORIGINS=https://<domain>
PLT_MAIL_BACKEND=smtp
PLT_MAIL_FROM="Pesticide Litigation Tracker <plt@<domain>>"
PLT_SMTP_HOST=<the domain mailbox provider's submission host>
PLT_SMTP_PORT=587
PLT_SMTP_USERNAME=<mailbox user>
PLT_SMTP_PASSWORD=<mailbox password>
PLT_SMTP_STARTTLS=true
PLT_ADMIN_EMAIL=<who reads the review-queue notice>
PLT_REVIEW_API_TOKEN=<generate, or leave unset to keep the queue disabled>
PLT_LOG_FORMAT=json
```

Notes that matter:

- **`_check_production_safety` refuses to start** with the placeholder secret key, with
  `PLT_DEBUG=true`, or without `PLT_SUBSCRIPTION_ADDRESS_PEPPER`. The mail validator refuses
  `console` in production. You cannot accidentally ship the development configuration — worth
  knowing, so that a startup failure is read as the guard working rather than as a bug.
- **`PLT_SUBSCRIPTION_ADDRESS_PEPPER` is long-lived and unrotatable.** Generate it once, back it
  up with the same seriousness as the database, and never change it. Rotating it makes every
  pseudonymised unsubscribe unrecognisable.
- **Outbound port 25 is blocked by default on most VPS providers**, and a fresh server IP has no
  sending reputation. Both are avoided by relaying through the domain mailbox's own submission
  service on 587 with authentication — the configuration above, and the reason the mailbox
  decision was made this way.
- **`PLT_MAIL_BACKEND=smtp` sends real mail to real addresses.** Verify the whole subscription
  flow on `file` first, read the `.eml`, and only then switch.
- `PLT_CORPUS_STORE_DIR` is irrelevant here — no mirror on this box (§3.1). Leave it unset.

---

## 11. Backups

The corpus is reproducible from the mirror. **The subscriber table is not.** That asymmetry
sets the policy:

```bash
pg_dump --format=custom plt > /var/backups/plt/plt-$(date +%F).dump
```

Nightly, retained a month, and **copied off the machine** — a backup that exists only on the VM
does not survive the event you are insuring against. Restore-test it at least once; an untested
backup is a belief, not a backup.

---

## 12. Deploying a change

```bash
sudo -u plt -H bash
cd /srv/plt/app && git pull
/srv/plt/venv/bin/pip install -c backend/constraints.txt backend
cd backend && /srv/plt/venv/bin/alembic upgrade head
cd ../frontend && npm ci && npm run build
exit
systemctl restart plt-api
```

Then check, in order: `/api/health` reports `database: ok`; the map shows the expected case
counts; a case deep link opened cold returns the page rather than a 404.

---

## 13. Moving to a university VM

What transfers unchanged: every section from §5 to §12. Ubuntu, systemd, PostgreSQL, Caddy and
a git checkout are available on any managed Linux VM, and the application knows nothing about
its host.

What has to be renegotiated:

| Concern | On our own VM | On a WUR VM |
| --- | --- | --- |
| **Domain and DNS** | Ours to point | A `wur.nl` subdomain request through WUR IT — re-scope §1 |
| **TLS** | Caddy, automatic Let's Encrypt | Possibly an institutionally issued certificate; Caddy can serve one, but the automation goes away |
| **Mail** | Domain mailbox, SMTP submission on 587 | Likely a WUR relay, possibly IP-authenticated rather than password-authenticated — re-scope §10 |
| **SSH** | Tailscale, our keys | Their access model, probably a VPN and central accounts — §5.4 is the part most likely to be replaced wholesale |
| **Backups** | Our cron, our offsite copy | Possibly institutional backup; confirm it actually covers PostgreSQL and not just the filesystem |
| **Patching** | `unattended-upgrades` | Likely managed; confirm who reboots and when |
| **Data protection** | Our responsibility, EU-hosted | A DPIA conversation, and §14's retention questions become mandatory rather than open |

The migration itself is: provision, run §5–§10, restore the latest `pg_dump`, repoint DNS.
Because the corpus lives on the workstation, there is no large data movement — a second reason
for the split in §3, beyond storage cost.

---

## 14. Open items

Not blocking a preview deployment; blocking a public one.

- **Subscriber retention is undecided** (issue #75). `PLT_SUBSCRIBER_RETENTION_DAYS` and
  `PLT_SUBSCRIBER_UNCONFIRMED_EXPIRY_DAYS` are unset, and unset means *not enforced* — not a
  default, because a default here would be a policy nobody chose. The moment this instance
  holds real addresses, the project is retaining personal data under a policy that does not yet
  exist. `plt purge-subscribers` enforces whichever is set, so the code is ready; the decision
  is not.
- **The review queue's survival across a republish** (§3.5) needs deciding before the first
  methodology rebuild.
- **Classification is empty.** `topic` and `case_topic` hold no rows, so the topic filters on
  the All cases page render with nothing to select. Not a deployment problem, but it will be
  the first thing a scholar asks about.
- **Trigram indexes are not yet in a migration** (§4.1). Until they are, a fresh deployment gets
  correct search and slow search.
