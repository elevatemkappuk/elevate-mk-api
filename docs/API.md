# Elevate MK API
Status: Living Documentation
Last Updated: 2026-08-29

## Scope
This document describes the HTTP API currently implemented in the Django server repository.

## Current API Surface
Current base path and versioning convention:

- Base prefix: `/api/v1/`
- Current Elevate API routes are defined in `accounts.urls` and mounted from `config.urls`
- Machine-readable OpenAPI schema: `/api/schema/`
- Swagger UI: `/api/docs/`
- ReDoc: `/api/redoc/`
- CSRF bootstrap endpoint: `/api/v1/auth/csrf/`

Local development URLs when running `manage.py runserver` on the default port:

- Swagger UI: `http://127.0.0.1:8000/api/docs/`
- ReDoc: `http://127.0.0.1:8000/api/redoc/`
- OpenAPI schema: `http://127.0.0.1:8000/api/schema/`
- CSRF bootstrap endpoint: `http://127.0.0.1:8000/api/v1/auth/csrf/`
- Login endpoint: `http://127.0.0.1:8000/api/v1/auth/login/`
- Logout endpoint: `http://127.0.0.1:8000/api/v1/auth/logout/`
- Me endpoint: `http://127.0.0.1:8000/api/v1/auth/me/`

Environment-relative URL patterns:

- Swagger UI: `{BASE_URL}/api/docs/`
- ReDoc: `{BASE_URL}/api/redoc/`
- OpenAPI schema: `{BASE_URL}/api/schema/`
- CSRF bootstrap endpoint: `{BASE_URL}/api/v1/auth/csrf/`
- Login endpoint: `{BASE_URL}/api/v1/auth/login/`
- Logout endpoint: `{BASE_URL}/api/v1/auth/logout/`
- Me endpoint: `{BASE_URL}/api/v1/auth/me/`

Deployment note:

- The path structure above is current implementation.
- The hostname, scheme, and port will vary by environment such as local development, staging, and production.
- Treat the OpenAPI/UI paths as environment-relative rather than hard-coded to `127.0.0.1:8000`.

Currently implemented Elevate endpoints:

- `GET /api/v1/auth/csrf/`
- `POST /api/v1/auth/login/`
- `POST /api/v1/auth/logout/`
- `GET /api/v1/auth/me/`

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

## Content Type Expectations
Current implementation is serializer-based and expects request bodies appropriate for DRF parsing.

- `GET /api/v1/auth/csrf/`: no request body
- `POST /api/v1/auth/login/`: JSON body with `email` and `password`
- `POST /api/v1/auth/logout/`: no request fields are required
- `GET /api/v1/auth/me/`: no request body

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
  "staff_roles": []
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

## Planned API Areas
Planned, not yet implemented:

- broader people/domain APIs around the `Person` model
- additional first-party authenticated application endpoints under the `/api/v1/` convention
- separate authorization-aware staff CRM capabilities once the authorization model exists
