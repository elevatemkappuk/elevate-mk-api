# Elevate MK Authentication
Status: Living Documentation
Last Updated: 2026-08-29

## Current Authentication Architecture
Elevate currently separates human identity from authentication account data:

- `people.Person` is the human/person record.
- `accounts.User` is the Django authentication account.

Current identity rules:

- A `Person` may exist without a `User`.
- A `User` must have exactly one linked `Person`.
- `Person` is authoritative for the person's name.
- `User` does not store `username`, `first_name`, or `last_name`.

## Authentication Identifier
Current behavior:

- Email is the authentication identifier.
- `User.USERNAME_FIELD` is `email`.
- `email` is unique on the custom user model.

## Email Normalization
The repository currently normalizes auth email with `accounts.models.normalize_email_address()`:

1. Trim surrounding whitespace.
2. Apply Django's `normalize_email()` behavior.
3. Lowercase the full address.

Current implications:

- Stored user email is lowercase.
- Authentication natural-key lookup is lowercase-normalized.
- Login input is normalized before calling Django `authenticate()`.
- Superuser creation also normalizes the email used for both `User.email` and the auto-created `Person.primary_email`.

## Password Handling
Current behavior:

- Passwords are handled by Django's standard password system.
- `UserManager` uses `set_password()`, so raw passwords are not stored.
- The `createsuperuser` workflow uses Django's secure password prompt and password validation flow.
- The auth API never returns password fields.

## Account Activation
Current behavior:

- `User.is_active` defaults to `True`.
- Login rejects inactive users.
- Inactive login failures use the same generic error as other credential failures.

## Session Authentication
Elevate's current first-party web authentication strategy is Django server-side sessions/cookies rather than JWT.

Why this is the current choice:

- it fits first-party applications
- the server controls revocation
- the browser credential is an HttpOnly session cookie managed by Django
- it uses native Django authentication/session integration
- it avoids unnecessary access/refresh token lifecycle complexity at this stage

This does not prevent additional authentication mechanisms later if future clients justify them. Any future mechanism can still keep the same `Person` versus `User` domain distinction.

Current product direction recorded in the codebase:

- Elevate applications are intended to maintain independent login sessions at this stage
- shared-login SSO is not currently implemented

## Session Isolation Strategy
Architectural decision:

- Elevate applications are intended to maintain independent login sessions
- logging into one Elevate application should not intentionally create a shared-login or SSO session for another application
- Django Admin is a technical administration interface, not part of the Elevate Staff CRM authentication experience
- the authentication architecture should not rely on a shared parent-domain session cookie

Current implication:

- Staff CRM authentication and Django Admin authentication should be treated as separate browser-session concerns unless a future explicit SSO design changes that decision

## Cookie Domain Strategy
Architectural decision:

- Django session cookies should remain host-scoped
- do not broaden cookie scope to a parent domain merely to share login state across Elevate subdomains

Examples of what this architecture intentionally avoids:

- `SESSION_COOKIE_DOMAIN = ".elevatemk.org"`
- `SESSION_COOKIE_DOMAIN = ".staging.elevatemk.org"`

Why this matters:

- a parent-domain cookie can make the same session credential available to multiple Elevate subdomains
- that would undermine the intended independent-session behavior between separate applications and technical interfaces
- this repository does not currently document any explicit SSO requirement that would justify that tradeoff

## CSRF Bootstrap Endpoint
Current bootstrap endpoint:

- `GET /api/v1/auth/csrf/`

Current bootstrap flow:

1. The client calls `GET /api/v1/auth/csrf/`.
2. Django issues the `csrftoken` cookie.
3. The client reads the cookie value.
4. The client sends that value back in the `X-CSRFToken` header on unsafe requests such as login and logout.
5. Login can then succeed and establish the normal Django `sessionid` cookie.

This endpoint is public, does not create a login session, and does not authenticate the caller.

## How Login Works
Current login endpoint:

- `POST /api/v1/auth/login/`

Current login flow:

1. The client first obtains a `csrftoken` cookie from `GET /api/v1/auth/csrf/`.
2. The request sends `email` and `password` plus `X-CSRFToken`.
3. `LoginSerializer` normalizes the email.
4. Django `authenticate()` checks the credentials against the custom `User`.
5. Invalid credentials and inactive accounts are rejected with the same generic error.
6. On success, `LoginView` calls Django `login(request, user)`.
7. Django persists the authenticated user ID in the server-side session and returns a session cookie to the client.

Request-flow diagram:
```text
Client
  -> GET /api/v1/auth/csrf/
Server
  -> set csrftoken cookie
Client
  -> POST /api/v1/auth/login/ {email, password}
Server
  -> validate X-CSRFToken against csrftoken cookie
  -> normalize email
  -> authenticate against accounts.User
  -> login(request, user)
  -> create/update server-side session
  -> send authenticated session cookie
Client
  -> stores session cookie for later requests
```

## Session Cookie Behavior
Current implementation relies on Django's normal session framework:

- authenticated state is represented server-side
- the client sends the session cookie on later requests
- Django loads the session before view code runs
- `sessionid` remains HttpOnly and browser-managed
- the frontend must not read or manually manage `sessionid`
- the CSRF bootstrap flow relies on `csrftoken` remaining JavaScript-readable so the frontend can send `X-CSRFToken`
- Django session cookies are intended to remain host-scoped unless a future explicit SSO requirement changes that direction

This repository does not currently introduce a custom cookie format or token layer on top of Django sessions.

## Local Browser Development
Current local development assumption:

- Angular Staff CRM origin: `http://localhost:4200`
- Django API origin: `http://localhost:8000`

Current local browser configuration:

- CORS allows exactly `http://localhost:4200`
- credentialed cross-origin requests are enabled
- `CSRF_TRUSTED_ORIGINS` includes `http://localhost:4200`
- the frontend should call `GET /api/v1/auth/csrf/` first, then send the `csrftoken` value back in `X-CSRFToken`
- `sessionid` remains HttpOnly/browser-managed and is not exposed to JavaScript

Current local-development artifact:

- because Django Admin and the API may be served from the same Django host during development, logging into Django Admin on `localhost` can result in the browser reusing the same Django session for API requests on `localhost`

Important clarification:

- this is a local host-sharing artifact
- it is not, by itself, evidence of a Django security failure
- it does not match Elevate MK's intended independent-session application architecture

Practical isolated local testing convention:

- CRM/API flow: `localhost`
- Django technical admin: `http://127.0.0.1:8000/admin/`

Why that convention works:

- browsers treat `localhost` and `127.0.0.1` as different cookie hosts
- a session created for `127.0.0.1` is not automatically reused for `localhost`

Important development rule:

- do not mix `localhost` and `127.0.0.1` across frontend and backend URLs during a session-authenticated browser flow

Why that matters:

- CORS origin matching is exact
- CSRF trusted-origin matching is exact
- cookies and browser-origin behavior become harder to reason about if one side uses `localhost` and the other uses `127.0.0.1`

## Cross-Origin CRM/API Browser Flow
Architectural decision:

- separate CRM and API hostnames are compatible with Django session authentication
- cross-origin does not imply a shared cookie across all subdomains

Expected browser configuration for a CRM frontend talking to a separate API host:

- the exact CRM origin must be allowed by CORS
- credentialed CORS must be enabled
- the CRM origin must be included in `CSRF_TRUSTED_ORIGINS`
- the browser sends the API host's session cookie back to that API host
- the `csrftoken` and `X-CSRFToken` flow remains required for unsafe requests

This is different from parent-domain cookie sharing:

- the session cookie remains scoped to the API host
- the frontend can make credentialed requests to that API host
- the browser does not thereby expose the same session cookie to every Elevate subdomain

## Illustrative Staging Topology
Illustrative, not contractual:

- CRM frontend: `https://crm.staging.elevatemk.org`
- API: `https://api.staging.elevatemk.org`
- Django technical admin: `https://django-admin.staging.elevatemk.org`

Architecture principle for that topology:

- these are separate hostnames
- with host-scoped cookies, a session created for the Django Admin host is not automatically shared with the API host
- the CRM frontend would still need the expected CORS and CSRF configuration to talk to the API host

Status note:

- the exact final staging DNS, proxy, and TLS details are not yet documented here as contractual deployment configuration

## Production Principle
Architectural decision:

- the Staff CRM frontend and Django technical admin should remain independently addressable
- API authentication cookies should be scoped to the API host
- technical Django Admin authentication should not intentionally grant Staff CRM access
- Staff CRM operational authorization remains based on `StaffRole` and `StaffRoleAssignment`, not Django `is_staff` or `is_superuser`

Operational implication:

- staging and production isolation should be achieved through hostname and cookie-scope configuration
- do not introduce custom session middleware solely to solve the localhost same-host development artifact

## How Authenticated Requests Become `request.user`
Current request path:

1. The client sends the session cookie.
2. `SessionMiddleware` loads the session.
3. `AuthenticationMiddleware` resolves the logged-in user.
4. DRF session authentication uses that session-backed identity.
5. Protected API views receive an authenticated `request.user`.

Request-flow diagram:
```text
Client
  -> GET /api/v1/auth/me/ with session cookie
Django middleware
  -> load session
  -> resolve authenticated user
DRF session authentication
  -> attach authenticated request.user
View
  -> return current user/person summary plus active staff role codes
```

## Logout and Session Invalidation
Current logout endpoint:

- `POST /api/v1/auth/logout/`

Current logout flow:

1. An authenticated client sends the logout request.
2. `LogoutView` calls Django `logout(request)`.
3. Django clears the authenticated session state.
4. Subsequent protected requests from that client are anonymous unless a new login occurs.

Request-flow diagram:
```text
Client
  -> POST /api/v1/auth/logout/ with session cookie
Server
  -> logout(request)
  -> clear authenticated session state
Client
  -> later protected request is anonymous
```

## CSRF Protection
Current behavior:

- `CsrfViewMiddleware` is enabled globally.
- `GET /api/v1/auth/csrf/` uses Django's normal CSRF cookie issuance behavior.
- `LoginView` and `LogoutView` are explicitly decorated with `csrf_protect`.
- The API uses cookie/session authentication, so CSRF protection is required for state-changing requests.
- The OpenAPI/Swagger documentation does not bypass CSRF or change runtime authentication behavior.
- Swagger UI documents the session-authenticated endpoints, but browser-based testing against login/logout still needs a valid CSRF bootstrap request and matching `X-CSRFToken` header.

Why CSRF matters here:

- browsers automatically attach cookies
- without CSRF protection, a third-party site could potentially trigger unintended authenticated POST requests from a victim browser

Current implementation does not disable CSRF globally and does not switch to token/JWT auth.

## Anonymous 401 Behavior
Current protected API behavior:

- unauthenticated access to protected endpoints returns `401 Unauthorized`

Implementation note:

- the project uses a small DRF `SessionAuthentication` subclass that returns an authentication header so DRF responds with `401` instead of its default session-auth `403` for anonymous protected requests

## Superuser Creation Workflow
Current command behavior:

- `python manage.py createsuperuser` is overridden by the `accounts` app
- It asks for:
  - email
  - person first name
  - person last name
  - password and password confirmation

Current creation flow:

1. Collect email and person name fields.
2. Validate the password using Django's normal password validation flow.
3. Pass the data into `UserManager.create_superuser()`.
4. The manager auto-creates a linked `Person`.
5. `Person.primary_email` is set from the normalized auth email.
6. The `User` is created with `is_staff=True` and `is_superuser=True`.

This preserves the invariant that a human `User` always has a linked `Person`.

## Authentication vs Authorization
Current meaning of Django flags:

- `is_staff`: Django admin/framework access concept
- `is_superuser`: Django global framework permission bypass concept

These are not yet Elevate operational authorization rules.

Why the distinction matters:

- authentication answers who the user is
- authorization answers what the user is allowed to do in the application

Current `/api/v1/auth/me/` behavior:

- remains an identity/authentication endpoint for any authenticated user
- includes `staff_roles` so the client can see the authenticated user's active operational staff role codes
- does not become a staff-only endpoint
- does not use Django `is_staff` or `is_superuser` as Elevate operational authorization

## Planned Authorization
Planned, not yet implemented:

- Staff CRM authorization separated from authentication
- Elevate operational authorization based on `StaffRole` and `StaffRoleAssignment`

Those models, permissions, and enforcement rules are not currently implemented.
