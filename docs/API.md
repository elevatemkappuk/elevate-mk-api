# Elevate MK API
Status: Living Documentation
Last Updated: 2026-08-29

## Scope
This document describes the HTTP API currently implemented in the Django server repository.

## Current API Surface
Current base path and versioning convention:

- Base prefix: `/api/v1/`
- Current Elevate API routes are defined in `accounts.urls` and mounted from `config.urls`

Currently implemented Elevate endpoints:

- `POST /api/v1/auth/login/`
- `POST /api/v1/auth/logout/`
- `GET /api/v1/auth/me/`

No other Elevate API endpoints are currently implemented.

## Authentication Expectations
Current behavior:

- The API uses Django server-side session authentication.
- Protected endpoints expect an authenticated Django session cookie.
- Login establishes that session with `django.contrib.auth.login()`.
- Logout invalidates it with `django.contrib.auth.logout()`.

## Content Type Expectations
Current implementation is serializer-based and expects request bodies appropriate for DRF parsing.

- `POST /api/v1/auth/login/`: JSON body with `email` and `password`
- `POST /api/v1/auth/logout/`: no request fields are required
- `GET /api/v1/auth/me/`: no request body

The test suite exercises JSON requests for the auth endpoints.

## CSRF Requirements
Current behavior:

- Django `CsrfViewMiddleware` is enabled globally.
- `LoginView` and `LogoutView` are explicitly wrapped with `csrf_protect`.
- Because the API uses cookie/session authentication, state-changing requests should include a valid CSRF token.

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
  }
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

Fields not returned:

- password fields
- Django `is_staff`
- Django `is_superuser`
- any Elevate staff-role or CRM authorization data

Error responses:

| Status | When | Shape |
| --- | --- | --- |
| `401 Unauthorized` | no authenticated session | standard DRF not-authenticated response |

Security behavior:

- Reads `request.user` from the authenticated Django session.
- Exposes only a concise account/person summary.

## Planned API Areas
Planned, not yet implemented:

- broader people/domain APIs around the `Person` model
- additional first-party authenticated application endpoints under the `/api/v1/` convention
- separate authorization-aware staff CRM capabilities once the authorization model exists
