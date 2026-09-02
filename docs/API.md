# Elevate MK API
Status: Living Documentation
Last Updated: 2026-08-31

## Scope
This document describes the HTTP API currently implemented in the Django server repository.

## Current API Surface
Current base path and versioning convention:

- Base prefix: `/api/v1/`
- Current Elevate API routes are defined in `accounts.urls`, `people.urls`, `memberships.urls`, `professional_profiles.urls`, `skills.urls`, `interests.urls`, and `tags.urls`, all mounted from `config.urls`
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
- Tags endpoint: `http://localhost:8000/api/v1/tags/`
- Industries endpoint: `http://localhost:8000/api/v1/industries/`
- Person detail endpoint: `http://localhost:8000/api/v1/people/{person_id}/`
- Create Contact endpoint: `http://localhost:8000/api/v1/people/`
- Create Member endpoint: `http://localhost:8000/api/v1/people/members/`
- Archive Person endpoint: `http://localhost:8000/api/v1/people/{person_id}/archive/`
- Restore Person endpoint: `http://localhost:8000/api/v1/people/{person_id}/restore/`
- Person skills endpoint: `http://localhost:8000/api/v1/people/{person_id}/skills/`
- Person interests endpoint: `http://localhost:8000/api/v1/people/{person_id}/interests/`
- Person tags endpoint: `http://localhost:8000/api/v1/people/{person_id}/tags/`
- Person tag removal endpoint: `http://localhost:8000/api/v1/people/{person_id}/tags/{tag_id}/remove/`
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
- Tags endpoint: `{BASE_URL}/api/v1/tags/`
- Industries endpoint: `{BASE_URL}/api/v1/industries/`
- Person detail endpoint: `{BASE_URL}/api/v1/people/{person_id}/`
- Create Contact endpoint: `{BASE_URL}/api/v1/people/`
- Create Member endpoint: `{BASE_URL}/api/v1/people/members/`
- Archive Person endpoint: `{BASE_URL}/api/v1/people/{person_id}/archive/`
- Restore Person endpoint: `{BASE_URL}/api/v1/people/{person_id}/restore/`
- Person skills endpoint: `{BASE_URL}/api/v1/people/{person_id}/skills/`
- Person interests endpoint: `{BASE_URL}/api/v1/people/{person_id}/interests/`
- Person tags endpoint: `{BASE_URL}/api/v1/people/{person_id}/tags/`
- Person tag removal endpoint: `{BASE_URL}/api/v1/people/{person_id}/tags/{tag_id}/remove/`
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
- `POST /api/v1/people/`
- `POST /api/v1/people/members/`
- `GET /api/v1/skills/`
- `GET /api/v1/interests/`
- `GET /api/v1/tags/`
- `GET /api/v1/industries/`
- `GET /api/v1/people/{person_id}/`
- `PATCH /api/v1/people/{person_id}/`
- `POST /api/v1/people/{person_id}/archive/`
- `POST /api/v1/people/{person_id}/restore/`
- `GET /api/v1/people/{person_id}/skills/`
- `GET /api/v1/people/{person_id}/interests/`
- `GET /api/v1/people/{person_id}/tags/`
- `POST /api/v1/people/{person_id}/tags/`
- `POST /api/v1/people/{person_id}/tags/{tag_id}/remove/`
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
- successful login, failed credential authentication attempts, and authenticated logout now also write append-only `audit.AuditEvent` rows

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
- `GET /api/v1/tags/`: no request body
- `GET /api/v1/industries/`: no request body
- `GET /api/v1/people/{person_id}/`: path parameter only
- `GET /api/v1/people/{person_id}/skills/`: path parameter only
- `GET /api/v1/people/{person_id}/interests/`: path parameter only
- `GET /api/v1/people/{person_id}/tags/`: path parameter only
- `POST /api/v1/people/{person_id}/tags/`: path parameter plus JSON body
- `POST /api/v1/people/{person_id}/tags/{tag_id}/remove/`: path parameters only; request body should be omitted
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
- On successful authentication, writes a `LOGIN_SUCCEEDED` audit event.
- Rejected credential attempts that reach authentication write a `LOGIN_FAILED` audit event.
- Malformed requests that never reach credential authentication are not treated as `LOGIN_FAILED`.
- Audit capture stores no password, session key, cookie, CSRF token, or attempted email.
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

- Writes a `LOGOUT` audit event while the authenticated actor is still available, then clears the Django session.
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

Staff access lifecycle note:

- the current Staff role grant, reactivation, and revocation workflows are managed through Django Admin rather than a public CRM API endpoint
- successful new role grant now writes immutable `AuditEvent` action `STAFF_ROLE_ASSIGNED`
- successful reactivation of an existing revoked assignment row writes immutable `AuditEvent` action `STAFF_ROLE_REACTIVATED`
- successful revocation writes immutable `AuditEvent` action `STAFF_ROLE_REVOKED`
- rejected or no-op lifecycle attempts do not emit successful Staff Access audit events
- at least one active operational `CRM_ADMIN` assignment must remain; Django `is_staff` and `is_superuser` do not count toward this requirement
- revoking the final active `CRM_ADMIN` assignment, including through a Django Admin bulk action, is rejected atomically with no successful revocation audit event
- the canonical `CRM_ADMIN` StaffRole cannot be deactivated
- `/api/v1/auth/me/` response shape is unchanged

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
| `q` | no | empty | Case-insensitive search across Person identity fields, full name, ProfessionalProfile job title, and company |
| `relationship` | no | none | Repeated `CONTACT`, `ACTIVE_MEMBER`, or `FORMER_MEMBER` values |
| `location` | no | none | Repeated, trimmed, case-insensitive exact free-text locations |
| `industry` | no | none | Repeated canonical Industry IDs |
| `career_stage` | no | none | Repeated `STUDENT`, `EARLY_CAREER`, `MID_CAREER`, `SENIOR`, `LEADERSHIP`, `FOUNDER_BUSINESS_OWNER`, or `OTHER` values |
| `interest` | no | none | Repeated canonical Interest IDs; current active assignments only |
| `skill` | no | none | Repeated canonical Skill IDs; current active assignments only |
| `tag` | no | none | Repeated canonical Tag IDs; only active `PersonTag` assignments and active Tags count |
| `ordering` | no | `last_name` | Canonical `name`, `-name`, `created_at`, `-created_at`, `membership_joined_at`, `-membership_joined_at`, `updated_at`, `-updated_at`; existing first/last-name sort keys remain accepted for current client compatibility |
| `page` | no | `1` | Standard DRF page-number pagination |
| `page_size` | no | `25` | `25`, `50`, or `100` only |

`record_state` semantics:

- `active`: `BUSINESS` Persons with `archived_at IS NULL`
- `archived`: `BUSINESS` Persons with `archived_at IS NOT NULL`
- `all`: all `BUSINESS` Persons regardless of `archived_at`
- Invalid `record_state`, `ordering`, or unsupported `page_size` values return `400 Bad Request`
- Invalid relationship, career-stage, or numeric catalog filter syntax returns `400 Bad Request`

Repeated filter syntax and composition:

- Multi-select filters use repeated query keys, for example `?interest=2&interest=8&skill=4&tag=6`
- Values within the same category are ORed: `interest=2&interest=8` means Interest 2 **or** Interest 8
- Different categories are ANDed: the example above requires a matching Interest **and** Skill 4 **and** active Tag 6
- Relationship is derived from Membership: no Membership is `CONTACT`, ACTIVE Membership is `ACTIVE_MEMBER`, and FORMER Membership is `FORMER_MEMBER`
- Whitespace-only `q` behaves as no search filter; Notes, AuditEvents, Staff Access, and authentication data are never searched
- `membership_joined_at` sorts dated Memberships first and Contacts/null dates last in both directions; every supported ordering has a stable Person ID tie-breaker
- Related classification filters use database `EXISTS` subqueries, so result rows and pagination counts are not duplicated by multi-valued assignments

Examples:

- Contacts: `/api/v1/people/?relationship=CONTACT`
- Active Members in Industry 5: `/api/v1/people/?relationship=ACTIVE_MEMBER&industry=5`
- Either Interest 2 or 8: `/api/v1/people/?interest=2&interest=8`
- Either Tag 3 or 7 with Skill 11: `/api/v1/people/?tag=3&tag=7&skill=11`
- Archived former Members: `/api/v1/people/?record_state=archived&relationship=FORMER_MEMBER`
- Professional search: `/api/v1/people/?q=engineer` or `/api/v1/people/?q=Microsoft`

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

## Endpoint: `GET /api/v1/tags/`
Purpose:
- Return the active canonical Tag taxonomy for internal CRM classification.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` required
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Behavior:

- Only active Tags are returned
- Results are ordered deterministically by `display_order`, `name`, then `id`
- The endpoint is intentionally unpaginated because it is a small controlled lookup collection
- Tag definitions are canonical taxonomy values and should be deactivated rather than treated as disposable
- Tags are internal CRM classification data, not member-facing profile fields
- Tags do not imply a completed workflow, task, reminder, availability, or commitment

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
    "name": "Potential Speaker",
    "slug": "potential-speaker"
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

## Endpoint: `GET /api/v1/people/{person_id}/tags/`
Purpose:
- Return the active internal CRM Tags for a single CRM-visible `BUSINESS` Person.

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
- A `BUSINESS` Person with no active Tags returns `200 OK` with `[]`

Tag-read behavior:

- only active `PersonTag` assignments whose canonical `Tag` definition is also active appear in the response
- inactive `PersonTag` rows remain stored for lifecycle/audit purposes but are omitted from this normal CRM read
- inactive `Tag` definitions remain stored and may still be referenced historically, but are omitted from this normal CRM read
- response order is deterministic by `display_order`, `name`, then `id`
- Tags are internal CRM classification data and are not member-facing profile fields

Returned fields:

- `id`
- `name`
- `slug`

Fields intentionally not exposed:

- `PersonTag` internal IDs
- `is_active`
- `assigned_by`
- `assigned_at`
- `removed_by`
- `removed_at`
- `description`

Example response:
```json
[
  {
    "id": 8,
    "name": "VIP",
    "slug": "vip"
  }
]
```

## Endpoint: `POST /api/v1/people/{person_id}/tags/`
Purpose:
- Create or reactivate a lifecycle-aware `PersonTag` assignment for an active CRM-visible `BUSINESS` Person.

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
- only active canonical Tags may be assigned or reactivated
- the request mutates `PersonTag` only and does not edit Tag taxonomy rows

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tag` | integer | yes | Canonical Tag primary key |

Client must not supply:

- `person`
- `id`
- `is_active`
- `assigned_by`
- `assigned_at`
- `removed_by`
- `removed_at`
- `name`
- `slug`
- any unknown field

Lifecycle behavior:

- if no `PersonTag` exists, the endpoint creates one row and returns `201 Created`
- if an inactive `PersonTag` already exists, the endpoint reactivates that same row and returns `200 OK`
- reactivation refreshes `assigned_by` and `assigned_at`
- reactivation clears `removed_by` and `removed_at`
- if the `PersonTag` is already active, the endpoint returns `409 Conflict`
- `unique(person, tag)` remains authoritative and duplicate creation races are converted to controlled conflicts
- successful new assignment writes an immutable `AuditEvent` with action `TAG_ASSIGNED`
- successful reactivation writes an immutable `AuditEvent` with action `TAG_REACTIVATED`
- rejected or conflicting requests do not emit a successful lifecycle audit event

Tag validation behavior:

- nonexistent Tag IDs return `400 Bad Request`
- inactive Tag definitions return `400 Bad Request`
- inactive Tag definitions are not auto-reactivated
- an inactive historical `PersonTag` tied to an inactive Tag cannot be reactivated until the canonical Tag definition is active again

Example request:
```json
{
  "tag": 8
}
```

Example response:
```json
{
  "id": 8,
  "name": "VIP",
  "slug": "vip"
}
```

## Endpoint: `POST /api/v1/people/{person_id}/tags/{tag_id}/remove/`
Purpose:
- Mark an existing active `PersonTag` inactive for an active CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` is read-only and receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated users without one of those active CRM roles return `403 Forbidden`

Write rules:

- Only active `BUSINESS` Persons are writable
- archived `BUSINESS` Persons return `409 Conflict`
- `TECHNICAL` Person records return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- missing `PersonTag` assignments return `404 Not Found`
- already inactive `PersonTag` assignments return `409 Conflict`
- request body should be omitted

Removal lifecycle behavior:

- successful removal preserves the existing `PersonTag` row
- `is_active` becomes `False`
- `removed_by` and `removed_at` are set by backend lifecycle logic
- `assigned_by` and `assigned_at` are preserved
- removing an assignment does not delete the canonical Tag definition
- an active assignment tied to an inactive Tag definition may still be removed for cleanup
- repeated reassignment later reuses the same `PersonTag` row instead of creating a duplicate
- successful removal writes an immutable `AuditEvent` with action `TAG_REMOVED`
- rejected or conflicting requests do not emit a successful lifecycle audit event

Successful response:

- Status: `204 No Content`
- Empty body

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
- successful assignment writes an immutable `AuditEvent` with action `INTEREST_ASSIGNED`
- rejected operations do not emit a successful mutation audit event

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
- successful removal writes an immutable `AuditEvent` with action `INTEREST_REMOVED`
- rejected operations do not emit a successful mutation audit event

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
- successful assignment writes an immutable `AuditEvent` with action `SKILL_ASSIGNED`
- rejected operations do not emit a successful mutation audit event

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
- successful removal writes an immutable `AuditEvent` with action `SKILL_REMOVED`
- rejected operations do not emit a successful mutation audit event

Successful response:

- Status: `204 No Content`
- empty body

## Person Write Lifecycle

All Person write endpoints require an authenticated session plus an active `CRM_ADMIN` or `CRM_MANAGER` role. `CRM_VIEWER` and authenticated non-staff users receive `403`; anonymous requests receive `401`. Django `is_staff` and `is_superuser` are not CRM write grants.

Only CRM-visible `BUSINESS` Persons are created or mutated. `record_type`, identifiers, timestamps, User fields, staff-role fields, Membership status, and unrelated domain data are server-controlled or rejected. There is no Person delete endpoint: archive and restore are the lifecycle.

### Endpoint: `POST /api/v1/people/`

Creates an active `BUSINESS` Contact with no Membership. Required fields are `first_name` and `last_name`; optional Person-owned fields are `primary_email`, `mobile`, `location`, `age_range`, and `gender`. `age_range` accepts `UNDER_25`, `25_29`, `30_34`, `35_39`, `40_45`, or `OVER_45`; `gender` accepts `MALE`, `FEMALE`, `NON_BINARY`, `TRANSGENDER`, or `OTHER`. The response is `201 Created` with the standard Person representation.

Before persistence, the API checks existing `BUSINESS` records, including archived records, for an exact normalized email (trimmed, lowercase) and a conservatively normalized mobile (trimmed with spaces, hyphens, and parentheses removed). Name alone never blocks creation. A collision returns `409 Conflict` with safe candidate summaries:

```json
{
  "detail": "A possible existing CRM Person was found.",
  "code": "IDENTITY_COLLISION",
  "collision": {"collision": "EMAIL_COLLISION", "person_ids": [14]},
  "candidates": [{"id": 14, "first_name": "Amina", "last_name": "Zulu", "primary_email": "amina@example.com", "mobile": "991000001", "archived_at": null}]
}
```

`collision.collision` is `MOBILE_COLLISION`, `EMAIL_COLLISION`, or `EMAIL_AND_MOBILE_COLLISION`. The client may retry only after an authorized staff member explicitly confirms a separate Person using `confirm_identity_override: true` and the exact safe `reviewed_collision` object from the 409 response. The server recomputes the evidence; changed candidates or collision type return `IDENTITY_COLLISION_STALE` with a refreshed safe candidate list and create nothing. Raw email or mobile is not used as confirmation evidence.

TECHNICAL records are never duplicate candidates. `PERSON_CREATED` is written in the same transaction as the Person. Intentional overrides retain that event and add only collision type and matched Person IDs to its metadata.

### Endpoint: `POST /api/v1/people/members/`

Creates a new `BUSINESS` Person and their first Membership atomically. It accepts the Contact fields above plus required `joined_at` (`YYYY-MM-DD`) and `membership_source`. The server forces Membership `status` to `ACTIVE` and `ended_at` to `null`.

The endpoint returns `201 Created` with the standard Person representation. It emits both `PERSON_CREATED` and `MEMBERSHIP_CREATED`; a Person, Membership, or either audit persistence failure rolls back the whole workflow. It uses the same collision and explicit reviewed-override contract as Contact creation, so an intentional duplicate can never create a partial Person if Membership creation later fails.

This is distinct from `POST /api/v1/people/{person_id}/membership/`, which makes an already-existing Contact a Member.

### Endpoint: `PATCH /api/v1/people/{person_id}/`

Edits only `first_name`, `last_name`, `primary_email`, `mobile`, `location`, `age_range`, and `gender` for an active `BUSINESS` Person. Unknown and server-managed fields are rejected. `age_range` accepts `UNDER_25`, `25_29`, `30_34`, `35_39`, `40_45`, or `OVER_45`; `gender` accepts `MALE`, `FEMALE`, `NON_BINARY`, `TRANSGENDER`, or `OTHER`. Both remain optional. Archived Persons return `409`; TECHNICAL and missing Persons return `404`.

When email or mobile changes, duplicate detection excludes the current Person but includes archived BUSINESS candidates. Real changes emit one `PERSON_UPDATED` event containing only changed Person fields. A true no-op PATCH returns `200 OK` without saving or emitting audit noise.

### Endpoint: `POST /api/v1/people/{person_id}/archive/`

Archives an active `BUSINESS` Person by setting `archived_at`; it requires an empty request body and returns the canonical Person representation. An already archived Person returns `409`; TECHNICAL and missing Persons return `404`. `PERSON_ARCHIVED` is emitted in the same transaction.

Archive does not disable a User, end Membership, change Staff Access, or alter ProfessionalProfile, Skills, Interests, Tags, Notes, or existing AuditEvents.

### Endpoint: `POST /api/v1/people/{person_id}/restore/`

Restores an archived `BUSINESS` Person by clearing `archived_at`; it requires an empty request body and returns the canonical Person representation. An already active Person returns `409`; TECHNICAL and missing Persons return `404`. `PERSON_RESTORED` is emitted in the same transaction and does not recreate or alter related domain state.

### Transactions and duplicate limits

Edits, archives, and restores lock the resolved Person with `select_for_update()` inside `transaction.atomic()`. Creation checks and all authoritative writes/audits run transactionally. `primary_email` and `mobile` deliberately have no database uniqueness constraint because historical CRM identity ambiguity is allowed. Therefore application-level duplicate detection is a strong operational guard, not an absolute guarantee against every simultaneous create race.

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
| `membership_source` | string | yes | One of `WEBSITE_FORM`, `MEMBERSHIP_FORM`, `STAFF`, `COMMUNITY_PLATFORM`, `OTHER` |

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
- a successful mutation also writes an immutable `AuditEvent` with action `MEMBERSHIP_CREATED`

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
- rejected or conflicting requests do not emit a successful lifecycle audit event

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
- successful `ACTIVE -> FORMER` transition also writes an immutable `AuditEvent` with action `MEMBERSHIP_ENDED`
- rejected or conflicting requests do not emit a successful lifecycle audit event

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
- successful creation writes an immutable `AuditEvent` with action `PROFESSIONAL_PROFILE_CREATED`
- rejected operations do not emit a successful mutation audit event

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
- successful PATCH writes `PROFESSIONAL_PROFILE_UPDATED` only when at least one writable business field actually changes after persistence
- no-op PATCH remains successful under the current API behavior but does not emit `PROFESSIONAL_PROFILE_UPDATED`
- rejected operations do not emit a successful mutation audit event

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
- `tags`: active Tag summary collection

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

Returned `tags` items when present:

- `id`
- `name`
- `slug`

Notes behavior:

- Notes are intentionally excluded from this overview projection
- sensitive Internal Notes remain a separate paginated CRM domain
- `/api/v1/people/{person_id}/overview/` does not expose note bodies, note lifecycle state, or note audit data

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

Tags behavior:

- `tags` is `[]` when the person has no active Tag classifications
- only active `PersonTag` assignments whose `Tag` definition is active appear in the overview projection
- `PersonTag` lifecycle metadata is intentionally omitted from the compact overview projection
- Tags are internal CRM classification data, not member-facing profile attributes
- V1 Tags do not imply tasks, reminders, workflow completion, availability, or commitment
- successful assignment and reactivation naturally make the Tag appear in overview
- successful removal naturally removes the Tag from overview while preserving the underlying `PersonTag` row
- inactive Tag definitions remain hidden from overview even if historical `PersonTag` rows still exist

Fields intentionally not exposed:

- `record_type`
- User/authentication internals
- Django `is_staff`
- Django `is_superuser`
- staff role-assignment internals

## Endpoint: `GET /api/v1/people/{person_id}/audit-history/`
Purpose:
- Return a read-only, paginated audit history projection for a single CRM-visible `BUSINESS` Person.

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

Scoping rule:

- the endpoint returns only existing immutable `AuditEvent` rows deliberately scoped to the requested Person
- primary cross-domain linkage uses `metadata.person_id = "{person_id}"`
- direct Person events are also included when `entity_type = "Person"` and `entity_id = "{person_id}"`
- unrelated authentication events are excluded unless they were explicitly recorded against the Person using one of those conventions
- unrelated `StaffRoleAssignment` events are excluded unless they were explicitly recorded against the Person using one of those conventions
- actor identity does not determine ownership of Person audit history

Viewer filtering rule:

- `CRM_VIEWER` may access Person Audit History
- `CRM_VIEWER` must not see Internal Note audit events
- Internal Note audit rows are excluded before pagination and count, not merely hidden in serialization
- a Viewer therefore cannot infer hidden note activity from `count`, `next`, `previous`, or result gaps

Pagination and ordering:

- default page size: `25`
- supported `page_size` values: `25`, `50`, `100`
- standard paginated response shape: `count`, `next`, `previous`, `results`
- ordering is newest first: `occurred_at DESC`, then `id DESC`

Response projection per event:

- `id`
- `action`
- `description`
- `actor`: `null` or `{ "id", "email" }`
- `occurred_at`
- `entity_type`
- `changes`

Safe projection rules:

- the response is not a raw `AuditEvent` dump
- `metadata` is not returned
- `request_id` is not returned
- `ip_address` is not returned
- `changes` is allowlist-projected by audited domain
- unrecognized or unsafe change keys are omitted
- Person Audit History never reconstructs Internal Note body history
- Internal Note events never expose note body or `archive_reason`
- no historical rows are synthesized or backfilled from current resource state

Description behavior:

- `description` is generated server-side from the audit action
- current Person-scoped actions use stable human-readable labels such as `Membership created`, `Tag assigned`, and `Internal note archived`
- Person lifecycle actions are also represented as `Person created`, `Person updated`, `Person archived`, and `Person restored`; their safe changes expose only approved Person-owned fields or lifecycle flags
- if a future supported action becomes Person-scoped without an explicit override, the API falls back to a deterministic label rather than crashing

## Endpoint: `GET /api/v1/people/{person_id}/notes/`
Purpose:
- Return a paginated collection of sensitive internal notes for a single CRM-visible `BUSINESS` Person.

Authentication and authorization:
- Authenticated Django session required
- Active `CRM_ADMIN` or `CRM_MANAGER` required
- `CRM_VIEWER` receives `403 Forbidden`
- Anonymous requests return `401 Unauthorized`
- Authenticated nonstaff users return `403 Forbidden`

Visibility and filtering:

- active and archived `BUSINESS` Persons are readable by direct ID
- `TECHNICAL` Person records return `404 Not Found`
- nonexistent Person IDs return `404 Not Found`
- default `record_state=active`
- supported `record_state` values:
  - `active`
  - `archived`
  - `all`
- unsupported `record_state` values return `400 Bad Request`
- default ordering is newest first: `created_at DESC`, then `id DESC`
- standard paginated response shape: `count`, `next`, `previous`, `results`

Returned note fields:

- `id`
- `body`
- `created_by` with `id` and `email`
- `created_at`
- `updated_at`
- `archived_at`
- `archived_by` with `id` and `email`, or `null`
- `archive_reason`

## Endpoint: `POST /api/v1/people/{person_id}/notes/`
Purpose:
- Create a new active internal note for an active CRM-visible `BUSINESS` Person.

Rules:

- accepts only `{ "body": "..." }`
- `created_by`, lifecycle fields, timestamps, and unknown fields are rejected
- blank or whitespace-only body returns `400 Bad Request`
- archived `BUSINESS` Person returns `409 Conflict`
- `TECHNICAL` or missing Person IDs return `404 Not Found`
- successful create writes immutable audit action `NOTE_CREATED`
- AuditEvent does not store note body

Successful response:

- Status: `201 Created`
- returns the serialized note

## Endpoint: `PATCH /api/v1/people/{person_id}/notes/{note_id}/`
Purpose:
- Edit the plain-text body of an existing active internal note.

Rules:

- only `body` is editable
- lifecycle fields use dedicated archive/restore endpoints
- archived note returns `409 Conflict`
- archived `BUSINESS` Person returns `409 Conflict`
- cross-Person note access returns `404 Not Found`
- a real body change writes `NOTE_UPDATED`
- no-op body PATCH remains successful but emits no `NOTE_UPDATED`
- AuditEvent does not store old or new note body; it records only that body changed

## Endpoint: `POST /api/v1/people/{person_id}/notes/{note_id}/archive/`
Purpose:
- Archive an active internal note without deleting it.

Rules:

- optional request body: `{ "archive_reason": "..." }`
- unrelated fields are rejected
- archived `BUSINESS` Person returns `409 Conflict`
- already archived note returns `409 Conflict`
- successful archive writes `NOTE_ARCHIVED`
- AuditEvent does not store note body or archive_reason

Successful response:

- Status: `200 OK`
- returns the serialized archived note

## Endpoint: `POST /api/v1/people/{person_id}/notes/{note_id}/restore/`
Purpose:
- Restore an archived internal note to active state.

Rules:

- request body should be empty
- unexpected fields return `400 Bad Request`
- archived `BUSINESS` Person returns `409 Conflict`
- already active note returns `409 Conflict`
- successful restore clears `archived_at`, `archived_by`, and `archive_reason`
- successful restore writes `NOTE_RESTORED`
- AuditEvent does not store note body or archive_reason

Successful response:

- Status: `200 OK`
- returns the serialized active note

Notes domain guardrails:

- there is no DELETE note endpoint
- Notes are not included in `/api/v1/people/{person_id}/overview/`
- rejected or conflicting note operations do not emit successful note audit events
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
  "interests": [],
  "tags": []
}
```

## Historical Import Batch Lifecycle
`GET /api/v1/imports/` and `GET /api/v1/imports/{batch_id}/` expose these staff-facing batch statuses:

- `PROCESSING`: workbook normalization and internal identity analysis are in progress.
- `READY_FOR_REVIEW`: CRM_ADMIN must resolve one or more identity decisions.
- `READY_FOR_IMPORT`: all identity decisions are resolved; the batch is eligible for authoritative import.
- `IMPORTED`: terminal state after a successful authoritative import.
- `FAILED`: structural ingestion or post-staging analysis failed safely.

Identity analysis is internal processing. A successful zero-review batch returns `READY_FOR_IMPORT`; a review batch transitions there after its final reconciliation decision.

## Endpoint: `POST /api/v1/imports/{batch_id}/import/`
Purpose:
- Synchronously perform the authoritative Membership Form import for one `READY_FOR_IMPORT` batch.

Authentication and authorization:
- Authenticated Django session and normal CSRF protection are required.
- Active operational `CRM_ADMIN` is required. `CRM_MANAGER`, `CRM_VIEWER`, and Django superuser or staff flags alone do not grant access.

Behavior:
- The endpoint delegates all mutation, locking, preflight, provenance, and audit behavior to the authoritative import service.
- A successful import returns `200 OK`, transitions the batch to `IMPORTED`, and returns the existing canonical batch DTO plus a summary of processed, created, matched, enriched, reused, and skipped counts.
- The summary does not contain raw staged rows, normalized source data, or source-row PII.
- A missing batch returns `404 Not Found`. A non-ready, already imported, unresolved, inconsistent, or conflicting membership state returns a safe `409 Conflict` response.

## Endpoint: `POST /api/v1/auth/password-reset/`
Purpose: request password-reset instructions for an eligible account.

- Public and CSRF-protected; rate limited to five requests per hour per client.
- Accepts `{ "email": "person@example.com" }` and normalizes email before lookup.
- Every well-formed request returns `200 OK` with `If an account exists for that email address, password reset instructions have been sent.`
- Only active Users with usable passwords receive a Brevo template email. Unknown, inactive, and unusable-password accounts receive the same response without delivery.
- The reset URL is generated from backend-only `CRM_FRONTEND_URL`; no URL, token, or provider data appears in the response.

## Endpoint: `POST /api/v1/auth/password-reset/confirm/`
Purpose: set a new password from a valid reset URL.

- Public and CSRF-protected; rate limited to ten requests per hour per client.
- Accepts `uid`, `token`, `new_password`, and `confirm_password`.
- Django password validators remain authoritative. A mismatch and validator failures return structured `400` validation errors.
- Invalid, expired, consumed, malformed, missing-user, and inactive-account links return `400` with `code: invalid_password_reset_token` and a controlled detail message.
- Successful reset returns `200 OK`, writes `PASSWORD_RESET` audit history, invalidates existing authenticated sessions through Django's password session-hash mechanism, and does not log the user in.

## Planned API Areas
Planned, not yet implemented:

- broader people/domain APIs around the `Person` model beyond the current read-only list endpoint
- additional first-party authenticated application endpoints under the `/api/v1/` convention
- separate authorization-aware staff CRM capabilities once the authorization model exists
