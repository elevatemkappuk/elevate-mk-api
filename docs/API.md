# Elevate MK API
Status: Living Documentation
Last Updated: 2026-08-31

## Scope
This document describes the HTTP API currently implemented in the Django server repository.

## Current API Surface
Current base path and versioning convention:

- Base prefix: `/api/v1/`
- Current Elevate API routes are defined in `accounts.urls`, `people.urls`, `memberships.urls`, `professional_profiles.urls`, `skills.urls`, and `interests.urls`, all mounted from `config.urls`
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
- Skills endpoint: `http://localhost:8000/api/v1/skills/`
- Interests endpoint: `http://localhost:8000/api/v1/interests/`
- Industries endpoint: `http://localhost:8000/api/v1/industries/`
- Person detail endpoint: `http://localhost:8000/api/v1/people/{person_id}/`
- Person skills endpoint: `http://localhost:8000/api/v1/people/{person_id}/skills/`
- Person interests endpoint: `http://localhost:8000/api/v1/people/{person_id}/interests/`
- Person skill removal endpoint: `http://localhost:8000/api/v1/people/{person_id}/skills/{skill_id}/`
- Person membership endpoint: `http://localhost:8000/api/v1/people/{person_id}/membership/`
- Person professional profile endpoint: `http://localhost:8000/api/v1/people/{person_id}/professional-profile/`
- Person overview endpoint: `http://localhost:8000/api/v1/people/{person_id}/overview/`

Environment-relative URL patterns:

- Swagger UI: `{BASE_URL}/api/docs/`
- ReDoc: `{BASE_URL}/api/redoc/`
- OpenAPI schema: `{BASE_URL}/api/schema/`
- CSRF bootstrap endpoint: `{BASE_URL}/api/v1/auth/csrf/`
- Login endpoint: `{BASE_URL}/api/v1/auth/login/`
- Logout endpoint: `{BASE_URL}/api/v1/auth/logout/`
- Me endpoint: `{BASE_URL}/api/v1/auth/me/`
- People endpoint: `{BASE_URL}/api/v1/people/`
- Skills endpoint: `{BASE_URL}/api/v1/skills/`
- Interests endpoint: `{BASE_URL}/api/v1/interests/`
- Industries endpoint: `{BASE_URL}/api/v1/industries/`
- Person detail endpoint: `{BASE_URL}/api/v1/people/{person_id}/`
- Person skills endpoint: `{BASE_URL}/api/v1/people/{person_id}/skills/`
- Person interests endpoint: `{BASE_URL}/api/v1/people/{person_id}/interests/`
- Person skill removal endpoint: `{BASE_URL}/api/v1/people/{person_id}/skills/{skill_id}/`
- Person membership endpoint: `{BASE_URL}/api/v1/people/{person_id}/membership/`
- Person professional profile endpoint: `{BASE_URL}/api/v1/people/{person_id}/professional-profile/`
- Person overview endpoint: `{BASE_URL}/api/v1/people/{person_id}/overview/`

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
- `GET /api/v1/skills/`
- `GET /api/v1/interests/`
- `GET /api/v1/industries/`
- `GET /api/v1/people/{person_id}/`
- `GET /api/v1/people/{person_id}/skills/`
- `GET /api/v1/people/{person_id}/interests/`
- `POST /api/v1/people/{person_id}/interests/`
- `DELETE /api/v1/people/{person_id}/interests/{interest_id}/`
- `POST /api/v1/people/{person_id}/skills/`
- `DELETE /api/v1/people/{person_id}/skills/{skill_id}/`
- `GET /api/v1/people/{person_id}/membership/`
- `POST /api/v1/people/{person_id}/membership/`
- `POST /api/v1/people/{person_id}/membership/end/`
- `GET /api/v1/people/{person_id}/professional-profile/`
- `GET /api/v1/people/{person_id}/overview/`

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
- `GET /api/v1/skills/`: no request body
- `GET /api/v1/interests/`: no request body
- `GET /api/v1/industries/`: no request body
- `GET /api/v1/people/{person_id}/`: path parameter only
- `GET /api/v1/people/{person_id}/skills/`: path parameter only
- `GET /api/v1/people/{person_id}/interests/`: path parameter only
- `POST /api/v1/people/{person_id}/interests/`: path parameter plus JSON body
- `DELETE /api/v1/people/{person_id}/interests/{interest_id}/`: path parameters only
- `POST /api/v1/people/{person_id}/skills/`: path parameter plus JSON body
- `DELETE /api/v1/people/{person_id}/skills/{skill_id}/`: path parameters only
- `GET /api/v1/people/{person_id}/membership/`: path parameter only
- `POST /api/v1/people/{person_id}/membership/`: path parameter plus JSON body
- `POST /api/v1/people/{person_id}/membership/end/`: path parameter plus JSON body
- `GET /api/v1/people/{person_id}/professional-profile/`: path parameter only
- `GET /api/v1/people/{person_id}/overview/`: path parameter only

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

## Endpoint: `GET /api/v1/industries/`
Purpose:
- Return the active canonical Industry taxonomy for CRM forms and future filters.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Behavior:

- Only active Industries are returned
- Results are ordered deterministically by `display_order`, `name`, then `id`
- The endpoint is intentionally unpaginated because it is a small controlled lookup collection

Returned fields:

- `id`
- `name`
- `slug`

Fields intentionally not exposed:

- `is_active`
- `display_order`
- `created_at`
- `updated_at`

Example response:
```json
[
  {
    "id": 1,
    "name": "Technology",
    "slug": "technology"
  }
]
```

## Endpoint: `GET /api/v1/interests/`
Purpose:
- Return the active canonical Interest taxonomy for CRM forms and overview reads.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Behavior:

- Only active Interests are returned
- Results are ordered deterministically by `display_order`, `name`, then `id`
- The endpoint is intentionally unpaginated because it is a small controlled lookup collection
- Interest definitions are canonical taxonomy values and should be deactivated rather than treated as disposable
- Interests mean what a Person is interested in, not what a Person can do and not how staff classify a Person
- V1 Interests do not encode willingness, availability, mentoring direction, or commitment

Returned fields:

- `id`
- `name`
- `slug`

Fields intentionally not exposed:

- `description`
- `is_active`
- `display_order`
- `created_at`
- `updated_at`

Example response:
```json
[
  {
    "id": 1,
    "name": "Networking",
    "slug": "networking"
  }
]
```

## Endpoint: `GET /api/v1/skills/`
Purpose:
- Return the active canonical Skill taxonomy for future CRM assignment UI and filtering.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Behavior:

- Only active Skills are returned
- Results are ordered deterministically by `display_order`, `name`, then `id`
- The endpoint is intentionally unpaginated because it is a small controlled lookup collection
- Skill definitions are canonical taxonomy values and should be deactivated rather than treated as disposable

Returned fields:

- `id`
- `name`
- `slug`

Fields intentionally not exposed:

- `description`
- `is_active`
- `display_order`
- `created_at`
- `updated_at`

Example response:
```json
[
  {
    "id": 1,
    "name": "Accounting",
    "slug": "accounting"
  }
]
```

## Endpoint: `GET /api/v1/people/{person_id}/skills/`
Purpose:
- Return the active assigned Skills for a single CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

People visibility rule:

- The endpoint operates only within the CRM `BUSINESS` Person domain
- Active and archived `BUSINESS` records are both retrievable by direct ID
- `TECHNICAL` Person records are never returned
- Requesting a `TECHNICAL` Person ID returns `404 Not Found`
- A missing Person ID also returns `404 Not Found`
- A `BUSINESS` Person with no Skills returns `200 OK` with `[]`

Skill activity rule:

- Only active Skill definitions appear in the response
- If a `PersonSkill` references a Skill that is later deactivated, the relationship remains stored but is omitted from this normal CRM read

Returned fields:

- `id`
- `name`
- `slug`

Fields intentionally not exposed:

- `PersonSkill` internal IDs
- assignment timestamps
- `description`
- proficiency, years, endorsement, or willingness metadata

Example response:
```json
[
  {
    "id": 16,
    "name": "Project Management",
    "slug": "project-management"
  }
]
```

## Endpoint: `GET /api/v1/people/{person_id}/interests/`
Purpose:
- Return the active assigned Interests for a single CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

People visibility rule:

- The endpoint operates only within the CRM `BUSINESS` Person domain
- Active and archived `BUSINESS` records are both retrievable by direct ID
- `TECHNICAL` Person records are never returned
- Requesting a `TECHNICAL` Person ID returns `404 Not Found`
- A missing Person ID also returns `404 Not Found`
- A `BUSINESS` Person with no Interests returns `200 OK` with `[]`

Interest activity rule:

- Only active Interest definitions appear in the response
- If a `PersonInterest` references an Interest that is later deactivated, the relationship remains stored but is omitted from this normal CRM read
- Response order is deterministic by `display_order`, `name`, then `id`

Returned fields:

- `id`
- `name`
- `slug`

Fields intentionally not exposed:

- `PersonInterest` internal IDs
- assignment timestamps
- `description`
- willingness, direction, availability, or commitment metadata

Example response:
```json
[
  {
    "id": 5,
    "name": "Technology",
    "slug": "technology"
  }
]
```

## Endpoint: `POST /api/v1/people/{person_id}/interests/`
Purpose:
- Create a `PersonInterest` assignment for an active CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Write rules:

- Only active `BUSINESS` Persons are writable
- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records are outside the CRM People domain and return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- only active canonical Interests may be assigned
- duplicate assignments return `409 Conflict`
- duplicate handling remains authoritative even if the stored assignment references an Interest later deactivated

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `interest` | integer | yes | Canonical Interest primary key |

Client must not supply:

- `person`
- `id`
- `created_at`
- `name`
- `slug`
- any unknown field

Validation and conflict behavior:

- nonexistent Interest IDs return `400 Bad Request`
- inactive Interests return `400 Bad Request`
- duplicate assignments return `409 Conflict`
- the endpoint creates only the relationship row; it does not edit canonical Interest taxonomy data
- Interest assignment means interest only and does not imply willingness, availability, mentoring direction, or commitment

Example request:
```json
{
  "interest": 5
}
```

Successful response:

- Status: `201 Created`
- returns the standard Interest summary representation

## Endpoint: `DELETE /api/v1/people/{person_id}/interests/{interest_id}/`
Purpose:
- Remove only the `PersonInterest` assignment between a CRM-visible `BUSINESS` Person and an Interest.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Delete rules:

- Only active `BUSINESS` Persons are writable
- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records are outside the CRM People domain and return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- missing assignments return `404 Not Found`
- inactive Interest assignments may still be removed by known `interest_id`
- the endpoint deletes only the `PersonInterest` relationship; it does not delete or deactivate the canonical Interest

Successful response:

- Status: `204 No Content`

## Endpoint: `POST /api/v1/people/{person_id}/skills/`
Purpose:
- Create a `PersonSkill` assignment for an active CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Write rules:

- Only active `BUSINESS` Persons are writable
- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records are outside the CRM People domain and return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- only active canonical Skills may be assigned
- duplicate assignments return `409 Conflict`

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `skill` | integer | yes | Canonical Skill primary key |

Client must not supply:

- `person`
- `id`
- `created_at`
- `name`
- `slug`
- any unknown field

Validation and conflict behavior:

- nonexistent Skill IDs return `400 Bad Request`
- inactive Skill IDs return `400 Bad Request`
- the route Person remains authoritative and cannot be overridden from the body
- existing `unique(person, skill)` remains authoritative for duplicate prevention
- duplicate races are converted from `IntegrityError` into controlled `409 Conflict`

Successful response:

- Status: `201 Created`
- returns the normal Skill summary representation

Example request:
```json
{
  "skill": 16
}
```

Example response:
```json
{
  "id": 16,
  "name": "Project Management",
  "slug": "project-management"
}
```

## Endpoint: `DELETE /api/v1/people/{person_id}/skills/{skill_id}/`
Purpose:
- Remove only the `PersonSkill` assignment between a CRM-visible `BUSINESS` Person and a Skill.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Write rules:

- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- missing assignments return `404 Not Found`
- nonexistent `skill_id` values also return `404 Not Found`

Removal semantics:

- deletes only the `PersonSkill` relationship
- does not delete or deactivate the canonical Skill definition
- does not modify `Person`, `Membership`, `ProfessionalProfile`, `User`, or Staff Access
- inactive Skill assignments may still be removed by known `skill_id`

Successful response:

- Status: `204 No Content`
- empty body

## Endpoint: `POST /api/v1/people/{person_id}/membership/`
Purpose:
- Execute the explicit business action `Make Member` for an existing CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

People visibility and lifecycle rule:

- The command operates only on CRM-visible `BUSINESS` Persons
- Active `BUSINESS` people are eligible
- Archived `BUSINESS` people are rejected with `409 Conflict`
- `TECHNICAL` Person records are never returned and still resolve to `404 Not Found`
- A missing Person ID also returns `404 Not Found`

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `joined_at` | date (`YYYY-MM-DD`) | yes | Historical membership join date; no backend default |
| `membership_source` | string | yes | One of `WEBSITE_FORM`, `STAFF`, `COMMUNITY_PLATFORM`, `OTHER` |

Backend-controlled fields:

- `status` is always created as `ACTIVE`
- `ended_at` is always created as `null`
- `person` is always derived from the route

Client must not supply:

- `status`
- `ended_at`
- `person`
- `id`
- `created_at`
- `updated_at`

Successful response:

- Status: `201 Created`
- Response body uses the standard Membership read representation

Example request:
```json
{
  "joined_at": "2024-04-12",
  "membership_source": "STAFF"
}
```

Example response:
```json
{
  "id": 9,
  "status": "ACTIVE",
  "joined_at": "2024-04-12",
  "ended_at": null,
  "membership_source": "STAFF",
  "created_at": "2026-08-30T12:00:00Z",
  "updated_at": "2026-08-30T12:00:00Z"
}
```

Conflict behavior:

- If the person already has an `ACTIVE` Membership, the request returns `409 Conflict`
- If the person already has a `FORMER` Membership, the request also returns `409 Conflict`
- The endpoint does not reactivate or overwrite existing memberships
- Rejoining is deferred because the current V1 model cannot preserve multiple membership periods safely

Validation behavior:

- `joined_at` is required
- `membership_source` is required
- invalid dates return `400 Bad Request`
- invalid source values return `400 Bad Request`
- client-controlled lifecycle fields are rejected rather than silently accepted

## Endpoint: `POST /api/v1/people/{person_id}/membership/end/`
Purpose:
- Execute the explicit business action `End Membership` for an existing CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `ended_at` | date (`YYYY-MM-DD`) | yes | Business end date; must not be earlier than `joined_at` |

Client must not supply:

- `status`
- `joined_at`
- `membership_source`
- `person`
- `id`
- `created_at`
- `updated_at`

Lifecycle behavior:

- valid transition is `ACTIVE -> FORMER`
- the existing Membership row is preserved
- `joined_at` is preserved
- `membership_source` is preserved
- `status` is changed to `FORMER`
- `ended_at` is set from the request

Successful response:

- Status: `200 OK`
- Response body uses the standard Membership read representation

Example request:
```json
{
  "ended_at": "2026-08-30"
}
```

Example response:
```json
{
  "id": 9,
  "status": "FORMER",
  "joined_at": "2024-04-12",
  "ended_at": "2026-08-30",
  "membership_source": "STAFF",
  "created_at": "2026-08-30T12:00:00Z",
  "updated_at": "2026-08-30T13:00:00Z"
}
```

Conflict behavior:

- BUSINESS person with no Membership -> `409 Conflict`
- Membership already `FORMER` -> `409 Conflict`
- archived BUSINESS person -> `409 Conflict`
- `TECHNICAL` Person -> `404 Not Found`
- nonexistent Person -> `404 Not Found`

Independence:

- does not create or modify `Person`
- does not create or modify `User`
- does not create or modify `StaffRoleAssignment`

## Endpoint: `GET /api/v1/people/{person_id}/professional-profile/`
Purpose:
- Return the ProfessionalProfile subresource for a CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Visibility rule:

- The endpoint operates only within the CRM People domain of `BUSINESS` Persons
- Active and archived `BUSINESS` records are both eligible by direct Person ID
- `TECHNICAL` Person records are never returned and resolve to `404 Not Found`
- A missing Person ID also returns `404 Not Found`
- A `BUSINESS` Person without a ProfessionalProfile also returns `404 Not Found`

Returned fields:

- `id`
- `job_title`
- `company`
- `industry`
- `career_stage`
- `linkedin_url`
- `created_at`
- `updated_at`

`industry` is either:

- `null`
- or an object with `id`, `name`, and `slug`

Allowed `career_stage` codes:

- `STUDENT` -> `Student`
- `EARLY_CAREER` -> `Early Career`
- `MID_CAREER` -> `Mid Career`
- `SENIOR` -> `Senior`
- `LEADERSHIP` -> `Leadership`
- `FOUNDER_BUSINESS_OWNER` -> `Founder / Business Owner`
- `OTHER` -> `Other`

The API continues to serialize the stable stored code rather than the display label.

Fields intentionally not exposed:

- `person`
- User/authentication internals
- Django `is_staff`
- Django `is_superuser`
- staff role-assignment internals

Example response:
```json
{
  "id": 12,
  "job_title": "Software Engineer",
  "company": "Example Ltd",
  "industry": {
    "id": 3,
    "name": "Technology",
    "slug": "technology"
  },
  "career_stage": "Senior individual contributor",
  "linkedin_url": "https://www.linkedin.com/in/example",
  "created_at": "2026-08-30T12:00:00Z",
  "updated_at": "2026-08-30T12:00:00Z"
}
```

## Endpoint: `POST /api/v1/people/{person_id}/professional-profile/`
Purpose:
- Create the one editable ProfessionalProfile resource for an active CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Write rules:

- Only active `BUSINESS` Persons are writable
- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records are outside the CRM People domain and return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- if a profile already exists for the Person, POST returns `409 Conflict`
- all fields are optional; an empty body may create an empty current-state profile
- no DELETE API exists

Writable request fields:

- `job_title`
- `company`
- `industry`
- `career_stage`
- `linkedin_url`

Client must not supply:

- `id`
- `person`
- `created_at`
- `updated_at`

Industry input:

- use the canonical Industry `id`
- `null` is allowed
- explicitly supplied Industries must exist and be active
- inactive Industries may remain on existing rows, but cannot be newly assigned

Career stage input:

- accepts only the stored codes:
  - `STUDENT`
  - `EARLY_CAREER`
  - `MID_CAREER`
  - `SENIOR`
  - `LEADERSHIP`
  - `FOUNDER_BUSINESS_OWNER`
  - `OTHER`

Example request:
```json
{
  "job_title": "Software Engineer",
  "company": "Example Ltd",
  "industry": 25,
  "career_stage": "MID_CAREER",
  "linkedin_url": "https://www.linkedin.com/in/example"
}
```

Successful response:

- Status: `201 Created`
- returns the standard ProfessionalProfile read representation with nested Industry

## Endpoint: `PATCH /api/v1/people/{person_id}/professional-profile/`
Purpose:
- Partially update the existing ProfessionalProfile for an active CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Patch rules:

- updates only the supplied fields
- does not auto-create on PATCH
- if no profile exists, PATCH returns `404 Not Found`
- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`

Clearing optional values:

- `{"industry": null}` clears Industry
- blank-capable text fields may be cleared with `""`
- `career_stage` may be cleared with `null` or `""`

Industry activity rule:

- active-status validation is applied only when the request explicitly supplies a non-null Industry
- an existing unchanged inactive Industry does not block unrelated PATCH updates

Example request:
```json
{
  "industry": null,
  "career_stage": "LEADERSHIP"
}
```

Successful response:

- Status: `200 OK`
- returns the standard ProfessionalProfile read representation with nested Industry

## Endpoint: `GET /api/v1/people/{person_id}/overview/`
Purpose:
- Return a read-only CRM projection optimized for the Person 360 screen.

Architectural distinction:

- `/api/v1/people/{person_id}/overview/` is an aggregate CRM read projection
- `/api/v1/people/{person_id}/` remains the authoritative Person resource
- `/api/v1/people/{person_id}/membership/` remains the authoritative Membership resource

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

People visibility rule:

- The endpoint operates only within the CRM `BUSINESS` Person domain
- Active and archived `BUSINESS` records are both retrievable by direct ID
- `TECHNICAL` Person records are never returned
- Requesting a `TECHNICAL` Person ID returns `404 Not Found`
- A missing Person ID also returns `404 Not Found`
- Unlike the standalone Membership endpoint, a valid `BUSINESS` Person without Membership still returns `200 OK`

Response structure:

- `person`: the same Person-owned fields returned by `GET /api/v1/people/{person_id}/`
- `relationship`: derived CRM relationship classification
- `membership`: Membership projection or `null`
- `professional_profile`: ProfessionalProfile projection or `null`
- `skills`: active Skill summary collection
- `interests`: active Interest summary collection

Relationship derivation:

- No Membership -> `CONTACT` / `Contact`
- `ACTIVE` Membership -> `ACTIVE_MEMBER` / `Active Member`
- `FORMER` Membership -> `FORMER_MEMBER` / `Former Member`

This relationship state is derived from Membership and is not persisted on `Person` or `Membership`.

Returned `person` fields:

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

Returned `membership` fields when present:

- `id`
- `status`
- `joined_at`
- `ended_at`
- `membership_source`
- `created_at`
- `updated_at`

Returned `professional_profile` fields when present:

- `id`
- `job_title`
- `company`
- `industry`
- `career_stage`
- `linkedin_url`
- `created_at`
- `updated_at`

Returned `skills` items when present:

- `id`
- `name`
- `slug`

Returned `interests` items when present:

- `id`
- `name`
- `slug`

Professional-profile behavior:

- `professional_profile` is `null` when no ProfessionalProfile exists
- `industry` is nested when present and `null` when absent
- `career_stage` is returned as the stable stored code when present
- ProfessionalProfile is current professional state, not employment history
- ProfessionalProfile is independent of `User`, `Membership`, and Staff access

Skills behavior:

- `skills` is `[]` when the person has no active assigned Skills
- only active Skill definitions appear in the overview projection
- `PersonSkill` assignment internals are intentionally omitted
- V1 Skills mean what a person can do; they do not imply interest, proficiency, years of experience, or willingness to participate
- successful assignment naturally makes the active Skill appear in overview
- successful removal naturally removes the Skill from overview

Interests behavior:

- `interests` is `[]` when the person has no active assigned Interests
- only active Interest definitions appear in the overview projection
- `PersonInterest` assignment internals are intentionally omitted
- V1 Interests mean what a person is interested in; they do not imply willingness, availability, mentoring direction, or commitment

Fields intentionally not exposed:

- `record_type`
- User/authentication internals
- Django `is_staff`
- Django `is_superuser`
- staff role-assignment internals
- speculative future Person Overview domain keys

Example response for a contact:
```json
{
  "person": {
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
  },
  "relationship": {
    "type": "CONTACT",
    "label": "Contact"
  },
  "membership": null,
  "professional_profile": null,
  "skills": [],
  "interests": []
}
```

## Planned API Areas
Planned, not yet implemented:

- broader people/domain APIs around the `Person` model beyond the current read-only list endpoint
- additional first-party authenticated application endpoints under the `/api/v1/` convention
- separate authorization-aware staff CRM capabilities once the authorization model exists
