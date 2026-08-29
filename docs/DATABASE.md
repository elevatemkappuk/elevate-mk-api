# Elevate MK Database
Status: Living Documentation
Last Updated: 2026-08-29

## Scope
This document describes the Elevate-owned database/domain structures currently implemented in the Django API codebase.

## Current Domain Model
The current Elevate-owned models are:

- `people.Person`
- `accounts.User`

Django-managed framework tables also exist because this project uses Django authentication, permissions, content types, admin, and server-side sessions. Those framework tables are not documented field-by-field here.

## Identity Relationship
Current rules:

- A `Person` may exist without a `User`.
- A `User` must belong to exactly one `Person`.
- `Person` is the authoritative record for human/profile identity.
- `User` is the authentication account.

Information deliberately stored on `Person` instead of `User`:

- Human name: `first_name`, `last_name`
- Person contact/profile details: `primary_email`, `mobile`, `location`
- Person demographic/profile placeholders: `age_range`, `gender`
- Person lifecycle metadata: `archived_at`, `created_at`, `updated_at`

Information deliberately stored on `User`:

- Authentication email
- Password hash
- Django framework account flags such as `is_active`, `is_staff`, `is_superuser`
- Django auth/permission relationships

## Relationship Diagram
```text
+-------------------+         0..1 <----- 1..1         +--------------------+
| people_person     |----------------------------------| accounts_user      |
+-------------------+          user.person_id          +--------------------+
| id (PK)           |                                  | id (PK)            |
| first_name        |                                  | email (unique)     |
| last_name         |                                  | password           |
| primary_email     |                                  | is_active          |
| mobile            |                                  | is_staff           |
| location          |                                  | is_superuser       |
| age_range         |                                  | last_login         |
| gender            |                                  | date_joined        |
| archived_at       |                                  | person_id (O2O)    |
| created_at        |                                  +--------------------+
| updated_at        |
+-------------------+
```

## Model: `people.Person`
Database table: `people_person`

Purpose:
- Stores the core human/person record independently from Django authentication.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `first_name` | `CharField(max_length=150)` | not null, `blank=False` | none | Required |
| `last_name` | `CharField(max_length=150)` | not null, `blank=False` | none | Required |
| `primary_email` | `EmailField` | `null=True`, `blank=True` | none | Optional, not unique |
| `mobile` | `CharField(max_length=50)` | not null, `blank=True` | empty string if left blank through forms/model defaults | Optional |
| `location` | `CharField(max_length=255)` | not null, `blank=True` | empty string if left blank through forms/model defaults | Optional free text |
| `age_range` | `CharField(max_length=100)` | not null, `blank=True` | empty string if left blank through forms/model defaults | Optional, no controlled choices yet |
| `gender` | `CharField(max_length=100)` | not null, `blank=True` | empty string if left blank through forms/model defaults | Optional, no controlled choices yet |
| `archived_at` | `DateTimeField` | `null=True`, `blank=True` | none | Optional archive marker |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on each save |

### Constraints and Behavior
Current implementation:

- Default primary key on `id`
- No unique constraints beyond the primary key
- Default model ordering is `last_name`, `first_name`, `id`
- `__str__()` returns `"first_name last_name"`

### Relationships
| Related Model | Relationship | Direction | on_delete | Notes |
| --- | --- | --- | --- | --- |
| `accounts.User` | one-to-one via `User.person` | reverse relation `person.user` | `PROTECT` on the `User` side | A person may have no user; at most one linked user |

### Domain Rules
Currently implemented rules:

- A person can exist without any linked authentication account.
- A person name is authoritative for display/name identity.
- No hard-delete behavior is implemented at the application level.
- Archiving is represented only by the optional `archived_at` field. No automatic archive workflow exists.

Planned, not yet implemented:

- Richer profile, membership, staff-role, interests, skills, tagging, notes, event, and engagement structures

## Model: `accounts.User`
Database table: `accounts_user`

Purpose:
- Stores the Django authentication account for a person.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `email` | `EmailField(max_length=254, unique=True)` | not null, `blank=False` | none | Authentication identifier |
| `password` | `CharField(max_length=128)` | not null | set via Django password hashing APIs | Stores password hash, not raw password |
| `last_login` | `DateTimeField` | `null=True`, `blank=True` | none | Managed by Django auth |
| `is_superuser` | `BooleanField` | not null | `False` | Django framework/admin flag |
| `is_staff` | `BooleanField` | not null | `False` | Django admin access flag |
| `is_active` | `BooleanField` | not null | `True` | Active/inactive account flag |
| `date_joined` | `DateTimeField` | not null | `django.utils.timezone.now` | Managed by Django account model |
| `person` | `OneToOneField(Person)` | not null, `blank=False` | none | Required link to `Person` |
| `groups` | `ManyToManyField(auth.Group)` | `blank=True` | none | Django auth framework relationship |
| `user_permissions` | `ManyToManyField(auth.Permission)` | `blank=True` | none | Django auth framework relationship |

Fields deliberately not present on this custom user model:

- `username`
- `first_name`
- `last_name`

### Constraints and Behavior
Current implementation:

- `email` is unique
- `person` is one-to-one and required
- `USERNAME_FIELD = "email"`
- `REQUIRED_FIELDS = []`
- `save()` normalizes email before persisting
- `get_full_name()` and `get_short_name()` read from the linked `Person`
- `__str__()` returns the normalized email

### Relationships
| Related Model | Relationship | Direction | on_delete | Notes |
| --- | --- | --- | --- | --- |
| `people.Person` | `OneToOneField` | forward relation `user.person` | `PROTECT` | A user must always point to one person |
| `auth.Group` | many-to-many | forward relation `user.groups` | Django-managed join table | Optional framework permissions grouping |
| `auth.Permission` | many-to-many | forward relation `user.user_permissions` | Django-managed join table | Optional framework permissions |

### Manager Behavior
Current `accounts.UserManager` behavior:

- Normalizes authentication email with `normalize_email_address()`
- `create_user()` requires either:
  - an existing `person`, or
  - `person_first_name` and `person_last_name`
- If no `person` is supplied, the manager creates one
- `create_superuser()` also ensures:
  - `is_staff=True`
  - `is_superuser=True`
  - `person_primary_email` defaults to the normalized auth email
- Natural-key lookup is normalized by email, so authentication and createsuperuser duplicate checks use normalized email

### Domain Rules
Currently implemented rules:

- Every `User` belongs to exactly one `Person`.
- A linked `Person` is required in the database and manager workflow.
- User email is normalized to lowercase before storage.
- Email uniqueness is enforced on the normalized stored value.
- Human names are not duplicated onto `User`; `Person` remains authoritative.

Planned, not yet implemented:

- Elevate-specific operational authorization such as staff CRM roles and assignments

## Django-Managed Framework Tables
Current project features imply framework-managed tables such as:

- authentication and permissions tables from `django.contrib.auth`
- content type tables from `django.contrib.contenttypes`
- admin log tables from `django.contrib.admin`
- session tables from `django.contrib.sessions`

These exist to support Django's framework behavior and are separate from Elevate's domain model.
