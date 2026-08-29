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

## How Login Works
Current login endpoint:

- `POST /api/v1/auth/login/`

Current login flow:

1. The request sends `email` and `password`.
2. `LoginSerializer` normalizes the email.
3. Django `authenticate()` checks the credentials against the custom `User`.
4. Invalid credentials and inactive accounts are rejected with the same generic error.
5. On success, `LoginView` calls Django `login(request, user)`.
6. Django persists the authenticated user ID in the server-side session and returns a session cookie to the client.

Request-flow diagram:
```text
Client
  -> POST /api/v1/auth/login/ {email, password}
Server
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

This repository does not currently introduce a custom cookie format or token layer on top of Django sessions.

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
  -> return current user/person summary
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
- `LoginView` and `LogoutView` are explicitly decorated with `csrf_protect`.
- The API uses cookie/session authentication, so CSRF protection is required for state-changing requests.

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

## Planned Authorization
Planned, not yet implemented:

- Staff CRM authorization separated from authentication
- Elevate operational authorization based on `StaffRole` and `StaffRoleAssignment`

Those models, permissions, and enforcement rules are not currently implemented.
