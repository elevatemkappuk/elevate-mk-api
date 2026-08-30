# Elevate MK Database
Status: Living Documentation
Last Updated: 2026-08-30

## Scope
This document describes the Elevate-owned database/domain structures currently implemented in the Django API codebase.

## Current Domain Model
The current Elevate-owned models are:

- `people.Person`
- `accounts.User`
- `memberships.Membership`
- `professional_profiles.Industry`
- `professional_profiles.ProfessionalProfile`
- `staff_access.StaffRole`
- `staff_access.StaffRoleAssignment`

Django-managed framework tables also exist because this project uses Django authentication, permissions, content types, admin, and server-side sessions. Those framework tables are not documented field-by-field here.

## Identity Relationship
Current rules:

- A `Person` may exist without a `User`.
- A `User` must belong to exactly one `Person`.
- `Person` is the authoritative record for human/profile identity.
- `User` is the authentication account.
- A `Person` may also have zero or one `ProfessionalProfile`.
- A `ProfessionalProfile` may optionally reference one canonical `Industry`.

Information deliberately stored on `Person` instead of `User`:

- Human name: `first_name`, `last_name`
- Person record classification: `record_type`
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
| updated_at        |                                           |
+-------------------+                                           | 1
         |                                                      |
         | 0..1                                                  | 0..*
         |                                                      |
         | 1                                                    |
+--------------------------+                  +----------------------------------+
| memberships_membership   |                  | staff_access_staffroleassignment |
+--------------------------+                  +----------------------------------+
| id (PK)                  |                  | id (PK)                          |
| person_id (O2O)          |                  | user_id (FK)                     |
| status                   |                  | role_id (FK)                     |
| joined_at                |                  | is_active                        |
| ended_at                 |                  | assigned_at                      |
| membership_source        |                  | assigned_by_id (FK, null)        |
| created_at               |                  | revoked_at (null)                |
| updated_at               |                  | revoked_by_id (FK, null)         |
+--------------------------+                  | created_at                       |
                                              | updated_at                       |
                                              +----------------------------------+
                                                            |
                                                            | 0..*
                                                            |
                                                            | 1
                                              +--------------------------+
                                              | staff_access_staffrole   |
                                              +--------------------------+
                                              | id (PK)                  |
                                              | code (unique)            |
                                              | name                     |
                                              | is_active                |
                                              | created_at               |
                                              | updated_at               |
                                              +--------------------------+
```

## Model: `people.Person`
Database table: `people_person`

Purpose:
- Stores the core human/person record independently from Django authentication.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `record_type` | `CharField(max_length=20)` | not null, `blank=False` | `BUSINESS` | Classification of business-domain versus technical/bootstrap identity |
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
- `record_type` choices:
  - `BUSINESS`
  - `TECHNICAL`
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
- `record_type=BUSINESS` is the default for newly created people.
- `record_type=TECHNICAL` is available for bootstrap or technical identities that still need a `Person`.
- `record_type` and `archived_at` are independent.
- `record_type` does not determine whether a person has a `User`, future `Membership`, or `StaffRoleAssignment`.
- No hard-delete behavior is implemented at the application level.
- Archiving is represented only by the optional `archived_at` field. No automatic archive workflow exists.

Planned, not yet implemented:

- Richer profile, membership, staff-role, interests, skills, tagging, notes, event, and engagement structures

Future People-view filtering rules:

- Normal People: `record_type = BUSINESS` and `archived_at IS NULL`
- Archived People: `record_type = BUSINESS` and `archived_at IS NOT NULL`
- `TECHNICAL` people are excluded from both normal and archived CRM People views

## Professional Profile Relationship
Current relationship:

```text
Person 0..1 ProfessionalProfile many..1 Industry
```

Current rules:

- ProfessionalProfile is current professional state, not employment history
- ProfessionalProfile belongs to `Person`, not `User`
- ProfessionalProfile is independent of `Membership` and `StaffRoleAssignment`
- Company remains plain text in V1
- Career Stage remains unconstrained text temporarily until the business taxonomy is confirmed
- No ProfessionalProfile is auto-created when a `Person`, `User`, `Membership`, or staff assignment is created
- Industry is controlled canonical data and referenced with `PROTECT`
- Industry records should be deactivated rather than treated as disposable taxonomy values

## Model: `professional_profiles.Industry`
Database table: `professional_profiles_industry`

Purpose:
- Stores the canonical Industry taxonomy reusable across professional profiles, filtering, onboarding, and analytics.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `name` | `CharField(max_length=255)` | not null, `blank=False` | none | Required canonical label |
| `slug` | `SlugField` | not null, `blank=False` | none | Required unique machine-readable identifier |
| `is_active` | `BooleanField` | not null | `True` | Inactive values remain stored but are excluded from the read API |
| `display_order` | `PositiveIntegerField` | not null | `100` | Controls deterministic presentation order |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- `slug` is unique
- default ordering is `display_order`, `name`, `id`
- no seed data migration invents a production taxonomy

## Model: `professional_profiles.ProfessionalProfile`
Database table: `professional_profiles_professionalprofile`

Purpose:
- Stores a person's current professional state independently from authentication, membership, and staff access.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `person` | `OneToOneField(Person)` | not null, `blank=False` | none | Required; a Person may have at most one ProfessionalProfile |
| `job_title` | `CharField(max_length=255)` | not null, `blank=True` | empty string | Optional plain-text job title |
| `company` | `CharField(max_length=255)` | not null, `blank=True` | empty string | Optional plain-text company field in V1 |
| `industry` | `ForeignKey(Industry)` | `null=True`, `blank=True` | none | Optional canonical industry reference |
| `career_stage` | `CharField(max_length=255)` | `null=True`, `blank=True` | none | Optional free text; controlled taxonomy is deferred |
| `linkedin_url` | `URLField` | not null, `blank=True` | empty string | Optional LinkedIn profile URL |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- one-to-one uniqueness on `person`
- `person` uses `PROTECT`, so deleting a referenced `Person` is blocked
- `industry` uses `PROTECT`, so deleting a referenced Industry is blocked
- default ordering is `person_id`

## Model: `memberships.Membership`
Database table: `memberships_membership`

Purpose:
- Stores the first Elevate membership relationship record for a person.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `person` | `OneToOneField(Person)` | not null, `blank=False` | none | A person has zero or one Membership in V1 |
| `status` | `CharField(max_length=20)` | not null, `blank=False` | none | Allowed values: `ACTIVE`, `FORMER` |
| `joined_at` | `DateField` | not null, `blank=False` | none | Historical business join date |
| `ended_at` | `DateField` | `null=True`, `blank=True` | none | End-of-membership business date for former members |
| `membership_source` | `CharField(max_length=30)` | not null, `blank=False` | none | Allowed values: `WEBSITE_FORM`, `STAFF`, `COMMUNITY_PLATFORM`, `OTHER` |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on each save |

### Constraints and Behavior
Current implementation:

- One-to-one uniqueness on `person`
- `person` uses `PROTECT`, so deleting a referenced `Person` is blocked
- Default ordering is `-joined_at`, `id`
- `__str__()` returns a person-oriented membership summary

### Relationships
| Related Model | Relationship | Direction | on_delete | Notes |
| --- | --- | --- | --- | --- |
| `people.Person` | `OneToOneField` | forward relation `membership.person` | `PROTECT` | Membership belongs to Person, not User |

### Validation Rules
Current implementation:

- `status` choices:
  - `ACTIVE`
  - `FORMER`
- `membership_source` choices:
  - `WEBSITE_FORM`
  - `STAFF`
  - `COMMUNITY_PLATFORM`
  - `OTHER`
- `ended_at` cannot be before `joined_at`
- `ACTIVE` membership cannot carry `ended_at`
- `FORMER` membership requires `ended_at`

### Domain Rules
Currently implemented rules:

- A `Person` may exist without a Membership and is then still a Contact
- Membership belongs to `Person`, not `User`
- A `User` may exist without a Membership
- Staff access does not imply Membership
- Membership is independent from Django `is_staff` / `is_superuser`
- No Membership is auto-created when a `Person`, `User`, or staff assignment is created
- Creating a Membership does not create a `User` or alter `StaffRoleAssignment`
- Ending a Membership reuses the same Membership row and does not modify `Person`, `User`, or `StaffRoleAssignment`
- No rejoin-history model exists yet
- Relationship labels such as Contact, Active Member, and Former Member are derived later rather than stored
- The CRM Person Overview projection derives relationship classification from Membership and does not persist `relationship_type`, `relationship_status`, or `is_member`

Consciously deferred:

- ending/reactivating/correcting memberships
- audit/history beyond the current model timestamps
- rejoin-history support

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
  - newly auto-created linked people default to `record_type=TECHNICAL`
- Natural-key lookup is normalized by email, so authentication and createsuperuser duplicate checks use normalized email

### Domain Rules
Currently implemented rules:

- Every `User` belongs to exactly one `Person`.
- A linked `Person` is required in the database and manager workflow.
- User email is normalized to lowercase before storage.
- Email uniqueness is enforced on the normalized stored value.
- Human names are not duplicated onto `User`; `Person` remains authoritative.
- `Person.record_type` does not control whether the linked user can authenticate.

Planned, not yet implemented:

- broader Elevate operational authorization beyond the current staff role foundation

## Model: `staff_access.StaffRole`
Database table: `staff_access_staffrole`

Purpose:
- Stores canonical operational staff role definitions for backend authorization.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `code` | `CharField(max_length=100, unique=True)` | not null, `blank=False` | none | Stable machine-readable authorization identifier |
| `name` | `CharField(max_length=255)` | not null, `blank=False` | none | Human-readable role name |
| `is_active` | `BooleanField` | not null | `True` | Inactive roles are excluded from active authorization evaluation |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- `code` is unique
- default ordering is by `code`
- canonical roles are seeded deterministically by migration:
  - `CRM_ADMIN`
  - `CRM_MANAGER`
  - `CRM_VIEWER`

### Relationships
| Related Model | Relationship | Direction | on_delete | Notes |
| --- | --- | --- | --- | --- |
| `staff_access.StaffRoleAssignment` | foreign key target | reverse relation `role.assignments` | `PROTECT` on the assignment side | Role deletion is blocked while referenced |

### Domain Rules
Currently implemented rules:

- `code` is the stable backend authorization identifier
- role activity is part of access evaluation
- Django Groups are not the primary Elevate operational role model

## Model: `staff_access.StaffRoleAssignment`
Database table: `staff_access_staffroleassignment`

Purpose:
- Grants canonical staff roles to specific authenticated users.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `user` | `ForeignKey(accounts.User)` | not null, `blank=False` | none | Subject account receiving the role |
| `role` | `ForeignKey(staff_access.StaffRole)` | not null, `blank=False` | none | Assigned canonical role |
| `is_active` | `BooleanField` | not null | `True` | Inactive assignment is excluded from authorization |
| `assigned_at` | `DateTimeField` | not null | `timezone.now` | Assignment timestamp |
| `assigned_by` | `ForeignKey(accounts.User)` | `null=True`, `blank=True` | none | Audit field for actor who assigned; nullable for bootstrap/system |
| `revoked_at` | `DateTimeField` | `null=True`, `blank=True` | none | Set when assignment is revoked |
| `revoked_by` | `ForeignKey(accounts.User)` | `null=True`, `blank=True` | none | Audit field for actor who revoked |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- unique constraint on `user` + `role`
- default ordering is `user_id`, `role__code`, `id`
- active authorization evaluation requires:
  - `is_active=True`
  - `revoked_at IS NULL`
  - `role.is_active=True`

### Relationships
| Related Model | Relationship | Direction | on_delete | Notes |
| --- | --- | --- | --- | --- |
| `accounts.User` | `ForeignKey` | forward relation `assignment.user` | `PROTECT` | Subject user deletion is blocked while assignments exist |
| `staff_access.StaffRole` | `ForeignKey` | forward relation `assignment.role` | `PROTECT` | Role deletion is blocked while assignments exist |
| `accounts.User` | `ForeignKey` | forward relation `assignment.assigned_by` | `SET_NULL` | Audit field may be null for bootstrap/system or if actor user is removed |
| `accounts.User` | `ForeignKey` | forward relation `assignment.revoked_by` | `SET_NULL` | Audit field may be null if actor user is removed |

### Lifecycle Rules
Currently implemented rules:

- normal lifecycle uses revocation rather than hard-delete behavior
- `revoke()` sets `is_active=False`, records `revoked_at`, and optionally records `revoked_by`
- manager method `assign_role(...)` reuses an existing user-role row when reactivating
- reactivation clears revocation state and restores `is_active=True`
- assignments belong to `User`, not `Person`
- a user may hold multiple different roles
- duplicate rows for the same `user` + `role` pair are prevented

## Django-Managed Framework Tables
Current project features imply framework-managed tables such as:

- authentication and permissions tables from `django.contrib.auth`
- content type tables from `django.contrib.contenttypes`
- admin log tables from `django.contrib.admin`
- session tables from `django.contrib.sessions`

These exist to support Django's framework behavior and are separate from Elevate's domain model.
