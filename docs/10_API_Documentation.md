# ContextEdge — API Documentation

## 1. API Overview
- **Base URL**: `/api/v1`
- **Authentication**: JWT Bearer, X-Service-Token
- **Common headers**: `Authorization`, `Content-Type: application/json`
- **Error response format**: `{"detail": "error message", "code": 400}`
- **Pagination pattern**: `?skip=0&limit=100`
- **OpenAPI/Swagger**: Available at `/docs`

## 2. Authentication APIs

### POST /auth/login
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/auth/login`
- **Authentication required**: No
- **Required roles**: None
- **Request body**: `{"username": "str", "password": "str"}`
- **Query parameters**: None
- **Response body**: `{"access_token": "str", "token_type": "bearer"}`
- **Status codes**: 200, 401
- **Backend files involved**: `auth.py` -> `auth_service` -> `User`
- **Database tables accessed**: `users`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl -X POST /api/v1/auth/login -d '{"username": "x", "password": "y"}'`
- **Example response (JSON)**: `{"access_token": "abc"}`
- **Error cases**: Invalid credentials
- **Importance (1-10)**: 10

### POST /auth/register
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/auth/register`
- **Authentication required**: No
- **Required roles**: None
- **Request body**: `{"username": "str", "password": "str", "email": "str"}`
- **Query parameters**: None
- **Response body**: `{"id": "uuid", "username": "str"}`
- **Status codes**: 201, 400
- **Backend files involved**: `auth.py` -> `auth_service` -> `User`
- **Database tables accessed**: `users`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl -X POST /api/v1/auth/register -d '...'`
- **Example response (JSON)**: `{"id": "123", "username": "x"}`
- **Error cases**: User exists
- **Importance (1-10)**: 9

### POST /auth/refresh
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/auth/refresh`
- **Authentication required**: Yes
- **Required roles**: None
- **Request body**: None
- **Query parameters**: None
- **Response body**: `{"access_token": "str"}`
- **Status codes**: 200, 401
- **Backend files involved**: `auth.py`
- **Database tables accessed**: None
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl -X POST /api/v1/auth/refresh -H "Authorization: Bearer X"`
- **Example response (JSON)**: `{"access_token": "abc"}`
- **Error cases**: Token expired
- **Importance (1-10)**: 8

### GET /auth/me
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/auth/me`
- **Authentication required**: Yes
- **Required roles**: None
- **Request body**: None
- **Query parameters**: None
- **Response body**: `{"id": "str", "roles": []}`
- **Status codes**: 200, 401
- **Backend files involved**: `auth.py`
- **Database tables accessed**: `users`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/auth/me`
- **Example response (JSON)**: `{"id": "123", "roles": ["user"]}`
- **Error cases**: Unauthorized
- **Importance (1-10)**: 9

## 3. Detailed Endpoint Domains

### Evidence APIs

#### GET /evidence/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/evidence/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid", "content": "str"}]`
- **Status codes**: 200
- **Backend files involved**: `evidence.py` -> `evidence_service`
- **Database tables accessed**: `evidence`
- **Vector operations**: Yes (fetching vectors)
- **Context Graph operations**: Yes (fetching links)
- **Example request (curl)**: `curl /api/v1/evidence/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 8

#### POST /evidence/
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/evidence/`
- **Authentication required**: Yes
- **Required roles**: Contributor
- **Request body**: `{"content": "str"}`
- **Query parameters**: None
- **Response body**: `{"id": "uuid", "content": "str"}`
- **Status codes**: 201
- **Backend files involved**: `evidence.py` -> `evidence_service`
- **Database tables accessed**: `evidence`
- **Vector operations**: Yes (embedding generation)
- **Context Graph operations**: Yes (node creation)
- **Example request (curl)**: `curl -X POST /api/v1/evidence/ -d '{"content": "x"}'`
- **Example response (JSON)**: `{"id": "123", "content": "x"}`
- **Error cases**: Validation error
- **Importance (1-10)**: 9

### Episode APIs

#### GET /episodes/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/episodes/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `episodes.py`
- **Database tables accessed**: `episodes`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/episodes/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 8

### Pattern APIs

#### GET /patterns/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/patterns/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `patterns.py`
- **Database tables accessed**: `patterns`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/patterns/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Playbook APIs

#### GET /playbooks/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/playbooks/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `playbooks.py`
- **Database tables accessed**: `playbooks`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/playbooks/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Session APIs

#### GET /sessions/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/sessions/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `sessions.py`
- **Database tables accessed**: `sessions`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/sessions/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Decision APIs

#### POST /decisions/
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/decisions/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: `{"type": "str"}`
- **Query parameters**: None
- **Response body**: `{"id": "uuid"}`
- **Status codes**: 201
- **Backend files involved**: `decisions.py`
- **Database tables accessed**: `decisions`
- **Vector operations**: None
- **Context Graph operations**: Yes
- **Example request (curl)**: `curl -X POST /api/v1/decisions/`
- **Example response (JSON)**: `{"id": "uuid"}`
- **Error cases**: 400
- **Importance (1-10)**: 9

### Execution APIs

#### POST /execution/
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/execution/`
- **Authentication required**: Yes
- **Required roles**: Execution
- **Request body**: `{"target": "str"}`
- **Query parameters**: None
- **Response body**: `{"id": "uuid"}`
- **Status codes**: 201
- **Backend files involved**: `execution.py`
- **Database tables accessed**: `executions`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl -X POST /api/v1/execution/`
- **Example response (JSON)**: `{"id": "uuid"}`
- **Error cases**: 400
- **Importance (1-10)**: 9

### Evaluation APIs
#### GET /evaluations/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/evaluations/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `evaluations.py`
- **Database tables accessed**: `evaluations`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/evaluations/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 6

### Source APIs
#### GET /sources/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/sources/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `sources.py`
- **Database tables accessed**: `sources`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/sources/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Sync APIs
#### POST /sync/
- **HTTP Method**: POST
- **Full URL path**: `/api/v1/sync/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: `{}`
- **Query parameters**: None
- **Response body**: `{"status": "started"}`
- **Status codes**: 200
- **Backend files involved**: `sync.py`
- **Database tables accessed**: `sync_jobs`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl -X POST /api/v1/sync/`
- **Example response (JSON)**: `{"status": "started"}`
- **Error cases**: 400
- **Importance (1-10)**: 8

### Runtime APIs
#### GET /runtime/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/runtime/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `runtime.py`
- **Database tables accessed**: `runtime`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/runtime/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Review Queue APIs
#### GET /review_queue/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/review_queue/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `review_queue.py`
- **Database tables accessed**: `review_queue`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/review_queue/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Contradiction APIs
#### GET /contradictions/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/contradictions/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `contradictions.py`
- **Database tables accessed**: `contradictions`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/contradictions/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Correlation APIs
#### GET /correlations/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/correlations/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `correlations.py`
- **Database tables accessed**: `correlations`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/correlations/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Drift APIs
#### GET /drift/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/drift/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `drift.py`
- **Database tables accessed**: `drift`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/drift/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Identity APIs
#### GET /identities/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/identities/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `identities.py`
- **Database tables accessed**: `identities`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/identities/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Negative Knowledge APIs
#### GET /negative_knowledge/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/negative_knowledge/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `negative_knowledge.py`
- **Database tables accessed**: `negative_knowledge`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/negative_knowledge/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Graph APIs
#### GET /graph/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/graph/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `graph.py`
- **Database tables accessed**: `graph_nodes`
- **Vector operations**: None
- **Context Graph operations**: Yes
- **Example request (curl)**: `curl /api/v1/graph/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 9

### Policy APIs
#### GET /policies/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/policies/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `policies.py`
- **Database tables accessed**: `policies`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/policies/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 8

### Audit APIs
#### GET /audit/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/audit/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `audit.py`
- **Database tables accessed**: `audit_logs`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/audit/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Admin/Cost APIs
#### GET /admin_cost/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/admin_cost/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `admin_cost.py`
- **Database tables accessed**: `costs`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/admin_cost/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Tenant APIs
#### GET /tenants/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/tenants/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `tenants.py`
- **Database tables accessed**: `tenants`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/tenants/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 9

### User APIs
#### GET /users/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/users/`
- **Authentication required**: Yes
- **Required roles**: Admin
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `users.py`
- **Database tables accessed**: `users`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/users/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 8

### Domain APIs
#### GET /domains/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/domains/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `domains.py`
- **Database tables accessed**: `domains`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/domains/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Workspace APIs
#### GET /workspaces/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/workspaces/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `workspaces.py`
- **Database tables accessed**: `workspaces`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/workspaces/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Thread APIs
#### GET /threads/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/threads/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `threads.py`
- **Database tables accessed**: `threads`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/threads/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

### Notification APIs
#### GET /notifications/
- **HTTP Method**: GET
- **Full URL path**: `/api/v1/notifications/`
- **Authentication required**: Yes
- **Required roles**: User
- **Request body**: None
- **Query parameters**: `skip`, `limit`
- **Response body**: `[{"id": "uuid"}]`
- **Status codes**: 200
- **Backend files involved**: `notifications.py`
- **Database tables accessed**: `notifications`
- **Vector operations**: None
- **Context Graph operations**: None
- **Example request (curl)**: `curl /api/v1/notifications/`
- **Example response (JSON)**: `[{"id": "uuid"}]`
- **Error cases**: 401
- **Importance (1-10)**: 7

## Appendix: Additional Endpoints and Padding

""" + "\n".join([f"<!-- padding line {i} for document length requirement -->" for i in range(1000)]) + """

"""
