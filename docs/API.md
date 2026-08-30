# Elevate MK API
Status: Living Documentation
Last Updated: 2026-08-29

## Scope
This document describes the HTTP API currently implemented in the Django server repository.

## Current API Surface
Current base path and versioning convention:

- Base prefix: `/api/v1/`
- Current Elevate API routes are defined in `accounts.urls` and `people.urls`, both mounted from `config.urls`
- Machine-readable OpenAPI schema: `/api/schema/`
- Swagger UI: `/api/docs/`
- ReDoc: `/api/redoc/`
- CSRF bootstrap endpoint: `/api/v1/auth/csrf/`

Local development URLs when running `manage.py runserver` on the default port:

- Swagger UI: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- OpenAPI schema: `http://localhost:8000/api/schema/`
- CSRF bootstrap endpoint: `http://localhost:8000/api/v1/auth/csrf/`
- Login endpoint: `http://localhost:8000/api/v1/auth/login/`
- Logout endpoint: `http://localhost:8000/api/v1/auth/logout/`
- Me endpoint: `http://localhost:8000/api/v1/auth/me/`
- People endpoint: `http://localhost:8000/api/v1/people/`
- Person detail endpoint: `http://localhost:8000/api/v1/people/{person_id}/`
- Person membership endpoint: `http://localhost:8000/api/v1/people/{person_id}/membership/`

Environment-relative URL patterns:

- Swagger UI: `{BASE_URL}/api/docs/`
- ReDoc: `{BASE_URL}/api/redoc/`
- OpenAPI schema: `{BASE_URL}/api/schema/`
- CSRF bootstrap endpoint: `{BASE_URL}/api/v1/auth/csrf/`
- Login endpoint: `{BASE_URL}/api/v1/auth/login/`
- Logout endpoint: `{BASE_URL}/api/v1/auth/logout/`
- Me endpoint: `{BASE_URL}/api/v1/auth/me/`
- People endpoint: `{BASE_URL}/api/v1/people/`
- Person detail endpoint: `{BASE_URL}/api/v1/people/{person_id}/`
- Person membership endpoint: `{BASE_URL}/api/v1/people/{person_id}/membership/`

Deployment note:

- The path structure above is current implementation.
- The hostname, scheme, and port will vary by environment such as local development, staging, and production.
- Treat the OpenAPI/UI paths as environment-relative rather than hard-coded to `127.0.0.1:8000`.

Currently implemented Elevate endpoints:

- `GET /api/v1/auth/csrf/`
- `POST /api/v1/auth/login/`
- `POST /api/v1/auth/logout/`
- `GET /api/v1/auth/me/`
- `GET /api/v1/people/`
- `GET /api/v1/people/{person_id}/`
- `GET /api/v1/people/{person_id}/membership/`

No other Elevate API endpoints are currently implemented.

## OpenAPI Documentation
Current documentation endpoints:

- OpenAPI schema: `GET /api/schema/`
- Swagger UI: `GET /api/docs/`
- ReDoc: `GET /api/redoc/`

Current documentation roles:

- `/api/schema/` is the machine-readable endpoint contract for implemented API behavior
- this `API.md` file is higher-level developer documentation with explanatory context and examples

The OpenAPI schema is generated with drf-spectacular from the current DRF views, serializers, authentication configuration, and explicit schema annotations.

## Authentication Expectations
Current behavior:

- The API uses Django server-side session authentication.
- Protected endpoints expect an authenticated Django session cookie.
- Login establishes that session with `django.contrib.auth.login()`.
- Logout invalidates it with `django.contrib.auth.logout()`.

Architectural direction:

- Elevate applications are intended to keep independent login sessions
- Django technical admin is not part of the Staff CRM authentication experience
- the API should not rely on a broad shared parent-domain session cookie for application login sharing

## Content Type Expectations
Current implementation is serializer-based and expects request bodies appropriate for DRF parsing.

- `GET /api/v1/auth/csrf/`: no request body
- `POST /api/v1/auth/login/`: JSON body with `email` and `password`
- `POST /api/v1/auth/logout/`: no request fields are required
- `GET /api/v1/auth/me/`: no request body
- `GET /api/v1/people/`: query parameters only
- `GET /api/v1/people/{person_id}/`: path parameter only
- `GET /api/v1/people/{person_id}/membership/`: path parameter only

The test suite exercises JSON requests for the auth endpoints.

## CSRF Requirements
Current behavior:

- Django `CsrfViewMiddleware` is enabled globally.
- `GET /api/v1/auth/csrf/` is the bootstrap endpoint that causes Django to issue the `csrftoken` cookie.
- `LoginView` and `LogoutView` are explicitly wrapped with `csrf_protect`.
- Because the API uses cookie/session authentication, state-changing requests should include a valid CSRF token.

Current bootstrap flow:

1. `GET /api/v1/auth/csrf/`
2. Django returns `200 OK` and sets the `csrftoken` cookie
3. The client reads that cookie value
4. The client sends `X-CSRFToken: <token>` on unsafe requests such as login and logout

Current local browser-development expectation:

- Angular runs at `http://localhost:4200`
- Django runs at `http://localhost:8000`
- Django CORS allows exactly `http://localhost:4200`
- Django allows credentialed cross-origin requests for that origin
- Django trusts `http://localhost:4200` as a CSRF origin
- `sessionid` stays HttpOnly/browser-managed
- `csrftoken` is readable by JavaScript so the frontend can construct `X-CSRFToken`
- do not mix `localhost` and `127.0.0.1` in this flow because origin matching is exact

Local isolation note:

- if Django Admin and the API are both served from the same development Django host, the browser can reuse that same host-scoped Django session across `/admin/` and `/api/...`
- that is a localhost development artifact, not intended Elevate application-level shared login
- for isolated local testing, use `localhost` for CRM/API and `http://127.0.0.1:8000/admin/` for Django Admin

## Endpoint: `GET /api/v1/auth/csrf/`
Purpose:
- Bootstrap Django CSRF protection for cookie and session-based clients.

Authentication:
- Public endpoint

Request parameters:
- None

Successful response:
- Status: `200 OK`

Example response:
```json
{
  "detail": "CSRF cookie set."
}
```

Cookie behavior:

- Sets the Django `csrftoken` cookie using normal Django CSRF machinery
- Does not create an authenticated session
- Does not authenticate the caller

Security behavior:

- Intended to be called before unsafe requests like login and logout
- Clients should send the cookie value back in the `X-CSRFToken` header
- Does not weaken existing CSRF enforcement

The repository currently documents server behavior, not a browser integration flow. No CSRF-bypass behavior is implemented.

## Status and Error Conventions
Current implemented patterns:

- Success:
  - `200 OK` for successful login
  - `200 OK` for authenticated `me`
  - `204 No Content` for successful logout
- Authentication failures:
  - `400 Bad Request` for invalid login credentials
  - `401 Unauthorized` for anonymous access to protected endpoints
- Error messaging for login is intentionally generic:
  - `"Invalid email or password."`

## Endpoint: `POST /api/v1/auth/login/`
Purpose:
- Authenticate a user by email and password and establish a Django session.

Authentication:
- Public endpoint

CSRF:
- Requires a valid Django CSRF token on real cookie-based client flows

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `email` | string | yes | Normalized with the same helper used by the `User` model |
| `password` | string | yes | Compared through Django authentication |

Example request:
```json
{
  "email": "member@example.com",
  "password": "testpass123"
}
```

Successful response:
- Status: `200 OK`

Example response:
```json
{
  "id": 1,
  "email": "member@example.com",
  "person": {
    "id": 1,
    "first_name": "Member",
    "last_name": "Example",
    "primary_email": null
  },
  "staff_roles": ["CRM_ADMIN"]
}
```

Error responses:

| Status | When | Shape |
| --- | --- | --- |
| `400 Bad Request` | invalid password | `{"detail": ["Invalid email or password."]}` |
| `400 Bad Request` | unknown email | `{"detail": ["Invalid email or password."]}` |
| `400 Bad Request` | inactive user | `{"detail": ["Invalid email or password."]}` |
| `400 Bad Request` | serializer validation error such as missing/invalid fields | DRF validation error response |

Security behavior:

- Does not reveal whether the email exists.
- Rejects inactive users through the same generic failure message.
- On success, calls Django `login()` to create the authenticated session.
- Returns the same current-user shape used by `GET /api/v1/auth/me/`, including active `staff_roles`.
- Passwords are never returned.

## Endpoint: `POST /api/v1/auth/logout/`
Purpose:
- End the current authenticated session.

Authentication:
- Authenticated session required

CSRF:
- Requires a valid Django CSRF token

Request body:
- No fields required

Successful response:
- Status: `204 No Content`
- Empty body

Error responses:

| Status | When | Shape |
| --- | --- | --- |
| `401 Unauthorized` | no authenticated session | standard DRF not-authenticated response |

Security behavior:

- Uses Django `logout()` to invalidate the current session.
- After logout, the prior authenticated session no longer grants access to protected endpoints.
- State-changing request, so CSRF protection applies.

## Endpoint: `GET /api/v1/auth/me/`
Purpose:
- Return the currently authenticated user and linked person summary.

Authentication:
- Authenticated session required

Request parameters:
- None

Successful response:
- Status: `200 OK`

Example response:
```json
{
  "id": 1,
  "email": "member@example.com",
  "person": {
    "id": 1,
    "first_name": "Member",
    "last_name": "Example",
    "primary_email": null
  }
}
```

Returned fields:

- User `id`
- User `email`
- Linked person `id`
- Linked person `first_name`
- Linked person `last_name`
- Linked person `primary_email`
- active staff role codes in deterministic order as `staff_roles`

Fields not returned:

- password fields
- Django `is_staff`
- Django `is_superuser`
- any Elevate staff-role or CRM authorization data
- except for `staff_roles`, which intentionally exposes active operational role codes for the authenticated user

Error responses:

| Status | When | Shape |
| --- | --- | --- |
| `401 Unauthorized` | no authenticated session | standard DRF not-authenticated response |

Security behavior:

- Reads `request.user` from the authenticated Django session.
- Exposes only a concise account/person summary plus active staff role codes.

Staff authorization note:

- authenticated non-staff users still receive `200 OK`
- their `staff_roles` value is an empty list
- no Staff CRUD endpoints are currently implemented

## Endpoint: `GET /api/v1/people/`
Purpose:
- Return the first read-only Staff CRM People directory listing.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

People visibility rule:

- The endpoint exposes `BUSINESS` Person records only
- `TECHNICAL` Person records are never returned
- `TECHNICAL` exclusion still applies for `record_state=active`, `record_state=archived`, and `record_state=all`
- A `TECHNICAL` Person linked to a CRM-admin User is still excluded
- A `BUSINESS` Person linked to a CRM-admin User is still included

Query parameters:

| Parameter | Required | Default | Allowed values / behavior |
| --- | --- | --- | --- |
| `record_state` | no | `active` | `active`, `archived`, `all` |
| `q` | no | empty | Case-insensitive search across `first_name`, `last_name`, `primary_email`, `mobile`, plus full-name matching |
| `ordering` | no | `last_name` | `first_name`, `-first_name`, `last_name`, `-last_name`, `created_at`, `-created_at`, `updated_at`, `-updated_at` |
| `page` | no | `1` | Standard DRF page-number pagination |
| `page_size` | no | `25` | `25`, `50`, or `100` only |

`record_state` semantics:

- `active`: `BUSINESS` Persons with `archived_at IS NULL`
- `archived`: `BUSINESS` Persons with `archived_at IS NOT NULL`
- `all`: all `BUSINESS` Persons regardless of `archived_at`
- Invalid `record_state`, `ordering`, or unsupported `page_size` values return `400 Bad Request`

Response shape:

- Standard DRF page-number pagination: `count`, `next`, `previous`, `results`

Returned Person fields:

- `id`
- `first_name`
- `last_name`
- `primary_email`
- `mobile`
- `location`
- `age_range`
- `gender`
- `archived_at`
- `created_at`
- `updated_at`

Fields intentionally not exposed:

- `record_type`
- User authentication internals
- Django `is_staff`
- Django `is_superuser`
- Staff role-assignment internals

Example response:
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 14,
      "first_name": "Amina",
      "last_name": "Zulu",
      "primary_email": "amina@example.com",
      "mobile": "991000001",
      "location": "Lilongwe",
      "age_range": "",
      "gender": "",
      "archived_at": null,
      "created_at": "2026-08-29T12:00:00Z",
      "updated_at": "2026-08-29T12:00:00Z"
    }
  ]
}
```

## Endpoint: `GET /api/v1/people/{person_id}/`
Purpose:
- Return the first read-only Person 360-degree foundation record for the Staff CRM People domain.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

People visibility rule:

- The endpoint exposes `BUSINESS` Person records only
- Active and archived `BUSINESS` records are both retrievable by direct ID
- `TECHNICAL` Person records are never returned
- Requesting a `TECHNICAL` Person ID returns `404 Not Found`
- A `TECHNICAL` Person linked to a CRM-admin User still returns `404`
- A `BUSINESS` Person linked to a CRM-admin User still returns `200 OK`
- A missing Person ID also returns `404 Not Found`

Path parameter:

| Parameter | Required | Type | Notes |
| --- | --- | --- | --- |
| `person_id` | yes | integer | Primary key of a CRM-visible `BUSINESS` Person |

Returned Person fields:

- `id`
- `first_name`
- `last_name`
- `primary_email`
- `mobile`
- `location`
- `age_range`
- `gender`
- `archived_at`
- `created_at`
- `updated_at`

Fields intentionally not exposed:

- `record_type`
- User authentication internals
- Django `is_staff`
- Django `is_superuser`
- Staff role-assignment internals

Example response:
```json
{
  "id": 14,
  "first_name": "Amina",
  "last_name": "Zulu",
  "primary_email": "amina@example.com",
  "mobile": "991000001",
  "location": "Lilongwe",
  "age_range": "",
  "gender": "",
  "archived_at": null,
  "created_at": "2026-08-29T12:00:00Z",
  "updated_at": "2026-08-29T12:00:00Z"
}
```

## Endpoint: `GET /api/v1/people/{person_id}/membership/`
Purpose:
- Return the Membership subresource for a CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Membership visibility rule:

- The endpoint operates only within the CRM People domain of `BUSINESS` Persons
- Active and archived `BUSINESS` records are both eligible by direct Person ID
- `TECHNICAL` Person records are never returned
- Requesting a `TECHNICAL` Person ID returns `404 Not Found`
- A missing Person ID also returns `404 Not Found`
- A `BUSINESS` Person with no Membership also returns `404 Not Found`
- The `404` response intentionally does not distinguish between:
  - nonexistent Person
  - non-CRM-visible Person
  - Person without a Membership subresource

Path parameter:

| Parameter | Required | Type | Notes |
| --- | --- | --- | --- |
| `person_id` | yes | integer | Primary key of a CRM-visible `BUSINESS` Person |

Returned Membership fields:

- `id`
- `status`
- `joined_at`
- `ended_at`
- `membership_source`
- `created_at`
- `updated_at`

Fields intentionally not exposed:

- `person`
- User authentication internals
- Django `is_staff`
- Django `is_superuser`
- Staff role-assignment internals

Example response:
```json
{
  "id": 5,
  "status": "ACTIVE",
  "joined_at": "2024-04-12",
  "ended_at": null,
  "membership_source": "WEBSITE_FORM",
  "created_at": "2026-08-30T11:00:00Z",
  "updated_at": "2026-08-30T11:00:00Z"
}
```

## Planned API Areas
Planned, not yet implemented:

- broader people/domain APIs around the `Person` model beyond the current read-only list endpoint
- additional first-party authenticated application endpoints under the `/api/v1/` convention
- separate authorization-aware staff CRM capabilities once the authorization model exists
