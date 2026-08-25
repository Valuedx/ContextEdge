# Username login and AutomationEdge tenant — Docker / server process

This is the server-side procedure to apply the username-only login change
and rename the default tenant to **AutomationEdge**.

`ae` in usernames means **AutomationEdge**.

## What changes

| Item | After this change |
| --- | --- |
| Login | Username + password only. Do **not** use an email (`@` is rejected). |
| Default tenant | Name `AutomationEdge`, slug `automationedge` (replaces legacy `default`). |
| Users in the database | Existing rows keep their password hashes. Usernames are set as below. |

Bootstrap usernames written into the `users` table:

| Username | Role |
| --- | --- |
| `superadmin-contextedge` | platform super admin |
| `tenantadmin-ae` | AutomationEdge tenant admin |
| `analyst-ae` | analyst |

Passwords are **not** reset. Use the password already stored (hashed) for that user, or set a new one in Settings after you can sign in.

## 1. Put the new code on the server

Deploy / pull the branch that contains:

- migration `0075_user_username_login`
- backend login on `username`
- frontend login field **Username**

If the backend image copies `alembic/` at build time (see `backend/Dockerfile`), **rebuild** the backend image so the container has revision `0075`:

```bash
docker compose -f docker-compose.dev.yml build backend celery-worker
docker compose -f docker-compose.dev.yml up -d
```

`docker-compose.dev.yml` mounts `backend/src` but **not** `backend/alembic`. A rebuild is required for the migration files unless you add an alembic volume yourself.

If you only run infrastructure from `docker-compose.yml` (Postgres / Redis / MinIO) and run the API on the host, skip the image rebuild and run Alembic on the host (step 2b).

## 2. Apply the database migration

The API returns **503** on `/ready` until `alembic_version` matches the code head. Workers also refuse to start if they are behind.

### 2a. Backend in Docker

```bash
docker compose -f docker-compose.dev.yml exec backend alembic upgrade head
docker compose -f docker-compose.dev.yml exec backend alembic current
```

Confirm `alembic current` includes `0075_user_username_login` (or whatever `alembic heads` prints after you pull — trust the command, not a number copied from an old doc).

### 2b. API on the host, Postgres in Docker

From the repo, with `DATABASE_URL` / `DATABASE_URL_SYNC` pointing at the Docker Postgres:

```bash
cd backend
alembic upgrade head
alembic current
```

Or from the repo root: `make migrate`.

## 3. Align the tenant and usernames

Seed does **not** invent passwords. It:

- sets tenant name/slug to AutomationEdge / `automationedge`
- assigns the three bootstrap usernames onto existing role rows
- optionally creates users only if `SEED_*_USERNAME` and `SEED_*_PASSWORD` are set in the environment

### 3a. Backend in Docker

```bash
docker compose -f docker-compose.dev.yml exec backend python -m contextedge.seed
```

### 3b. Host

```bash
cd backend
python dev.py seed
```

The command prints usernames found in the database. It does not print passwords.

## 4. Restart app containers

```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate backend frontend celery-worker
```

Rebuild the frontend image if it does not bind-mount `frontend/src`, so the login page sends `{ "username", "password" }`.

## 5. Verify

1. Open the login page. The field label is **Username**, not Email.
2. Sign in with `superadmin-contextedge` (or `tenantadmin-ae` / `analyst-ae`) and the existing password for that account.
3. Optional SQL check (Postgres container name may differ):

```bash
docker compose exec postgres psql -U postgres -d AEProdSupport -c "SELECT slug, name FROM tenants;"
docker compose exec postgres psql -U postgres -d AEProdSupport -c "SELECT username, status FROM users ORDER BY username;"
```

Use your real `POSTGRES_USER` / `POSTGRES_DB` from `.env` if they are not the defaults.

## Optional: first-time users via environment

Only if those rows do not exist yet. Do not put passwords in git.

```bash
# example — set in the shell or in a private env file, not in source
export SEED_SUPER_ADMIN_USERNAME=superadmin-contextedge
export SEED_SUPER_ADMIN_PASSWORD='...'
export SEED_TENANT_ADMIN_USERNAME=tenantadmin-ae
export SEED_TENANT_ADMIN_PASSWORD='...'
export SEED_ANALYST_USERNAME=analyst-ae
export SEED_ANALYST_PASSWORD='...'
docker compose -f docker-compose.dev.yml exec -e SEED_SUPER_ADMIN_USERNAME -e SEED_SUPER_ADMIN_PASSWORD backend python -m contextedge.seed
```

Pass the other `SEED_*` variables the same way.

## After login

Create further users in **Settings → Users** with a username such as `operator-ae` (letters, numbers, `.`, `_`, `-` only; no `@`).
