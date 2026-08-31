# Elevate MK Database
Status: Living Documentation
Last Updated: 2026-08-31

## Scope
This document describes the Elevate-owned database/domain structures currently implemented in the Django API codebase.

## Current Domain Model
The current Elevate-owned models are:

- `people.Person`
- `accounts.User`
- `memberships.Membership`
- `professional_profiles.Industry`
- `professional_profiles.ProfessionalProfile`
- `skills.Skill`
- `skills.PersonSkill`
- `interests.Interest`
- `interests.PersonInterest`
- `tags.Tag`
- `tags.PersonTag`
- `audit.AuditEvent`
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
- A `Person` may also have zero or many assigned `Skill` definitions through `PersonSkill`.
- A `Person` may also have zero or many assigned `Interest` definitions through `PersonInterest`.
- A `Person` may also have zero or many internal CRM `Tag` classifications through `PersonTag`.
- An authenticated `User` may also be referenced as the actor on zero or many immutable `AuditEvent` rows.

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

- Richer profile, membership, staff-role, interest, tagging, note, event, and engagement structures

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
- Career Stage is a small controlled application taxonomy implemented with Django `TextChoices`
- Career Stage is not a separately managed database entity
- `FOUNDER_BUSINESS_OWNER` is the stable stored code for the approved label `Founder / Business Owner`
- No ProfessionalProfile is auto-created when a `Person`, `User`, `Membership`, or staff assignment is created
- Industry is controlled canonical data and referenced with `PROTECT`
- Industry records should be deactivated rather than treated as disposable taxonomy values
- new or changed Industry assignments must use an active Industry
- an existing ProfessionalProfile may remain linked to an Industry that later becomes inactive
- successful ProfessionalProfile create and real update mutations are audited separately from the authoritative row state

## Skill Relationship
Current relationship:

```text
Person 0..* PersonSkill *..1 Skill
```

Current rules:

- Skill is a canonical taxonomy
- PersonSkill is the assignment relationship
- `unique(person, skill)` prevents duplicate assignment rows
- Skills mean what a Person can do
- Skills do not imply interest or willingness to participate
- V1 Skills carry no proficiency, years-of-experience, ranking, verification, or endorsement metadata
- inactive Skill definitions remain referentially valid through existing `PersonSkill` rows
- inactive Skill definitions are excluded from normal active CRM reads
- Skill definitions should be deactivated rather than treated as disposable taxonomy rows
- removing a `PersonSkill` deletes only the relationship row, not the canonical `Skill`
- successful PersonSkill assignment and removal mutations are audited separately from the authoritative relationship row

## Interest Relationship
Current relationship:

```text
Person 0..* PersonInterest *..1 Interest
```

Current rules:

- Interest is a canonical taxonomy
- PersonInterest is the assignment relationship
- `unique(person, interest)` prevents duplicate assignment rows
- Interests mean what a Person is interested in
- Interests are distinct from Skills and Tags
- V1 Interests do not encode willingness, availability, mentoring direction, ranking, or commitment
- inactive Interest definitions remain referentially valid through existing `PersonInterest` rows
- inactive Interest definitions are excluded from normal active CRM reads
- Interest definitions should be deactivated rather than treated as disposable taxonomy rows
- removing a `PersonInterest` deletes only the relationship row, not the canonical `Interest`
- successful PersonInterest assignment and removal mutations are audited separately from the authoritative relationship row

## Internal Note Relationship
Current relationship:

```text
Person 0..* InternalNote *..1 User(created_by)
Person 0..* InternalNote 0..1 User(archived_by)
```

Current rules:

- InternalNote is sensitive internal CRM context authored by staff about a Person
- InternalNote belongs directly to `Person`
- InternalNote does not require Membership, ProfessionalProfile, or a linked User on the Person
- body is plain text only in V1
- body remains authoritative on `InternalNote` and is never copied into `AuditEvent`
- `created_by` preserves original authorship
- normal lifecycle is active, archive, and restore; no hard delete
- archived state is derived from `archived_at`
- restore clears `archived_at`, `archived_by`, and `archive_reason`
- `archive_reason` remains authoritative on `InternalNote` and is not copied to generic audit payloads
- Notes are a separate sensitive domain and are intentionally excluded from Person Overview
- only `CRM_ADMIN` and `CRM_MANAGER` may access CRM Notes endpoints
- note mutations and their required audit rows share one transaction

## Tag Relationship
Current relationship:

```text
Person 0..* PersonTag *..1 Tag
```

Current rules:

- Tag is an internal CRM classification taxonomy
- PersonTag is the lifecycle-preserving assignment relationship
- `unique(person, tag)` prevents duplicate rows and reassignment reactivates the same row
- active assignments have `removed_by = null` and `removed_at = null`
- inactive assignments retain the row plus removal attribution
- new assignment creates the row with current assignment attribution
- removal preserves the row, sets `is_active = False`, and records `removed_by` plus `removed_at`
- reactivation preserves the row, sets `is_active = True`, refreshes `assigned_by` plus `assigned_at`, and clears `removed_by` plus `removed_at`
- Tags are distinct from Skills and Interests
- Tags do not imply completed workflows, tasks, reminders, availability, or commitment
- inactive Tag definitions and inactive assignments are excluded from normal CRM reads
- Tag definitions should be deactivated rather than treated as disposable taxonomy rows
- Tags must not be exposed on public or member-facing surfaces
- explicit AuditEvent integration is deferred

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
- the approved initial 29-row taxonomy is seeded by migration
- canonical rows are updated by slug if they already exist
- unrelated rows are not deleted by the seed migration

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
| `career_stage` | `CharField(max_length=255)` | `null=True`, `blank=True` | none | Optional controlled code from a small Django `TextChoices` taxonomy |
| `linkedin_url` | `URLField` | not null, `blank=True` | empty string | Optional LinkedIn profile URL |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- one-to-one uniqueness on `person`
- `person` uses `PROTECT`, so deleting a referenced `Person` is blocked
- `industry` uses `PROTECT`, so deleting a referenced Industry is blocked
- default ordering is `person_id`
- `career_stage` accepts only:
  - `STUDENT`
  - `EARLY_CAREER`
  - `MID_CAREER`
  - `SENIOR`
  - `LEADERSHIP`
  - `FOUNDER_BUSINESS_OWNER`
  - `OTHER`

## Model: `skills.Skill`
Database table: `skills_skill`

Purpose:
- Stores the canonical Skill taxonomy reusable across person assignment, filtering, onboarding, and analytics.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `name` | `CharField(max_length=255)` | not null, `blank=False` | none | Required canonical label |
| `slug` | `SlugField` | not null, `blank=False` | none | Required unique machine-readable identifier |
| `description` | `TextField` | not null, `blank=True` | empty string | Optional description |
| `is_active` | `BooleanField` | not null | `True` | Inactive values remain stored but are excluded from normal active CRM reads |
| `display_order` | `PositiveIntegerField` | not null | `100` | Controls deterministic presentation order |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- `slug` is unique
- default ordering is `display_order`, `name`, `id`
- the approved initial 27-row taxonomy is seeded by migration
- canonical rows are updated by slug if they already exist
- unrelated rows are not deleted by the seed migration
- existing descriptions are preserved during canonical reseeding because no approved descriptions are seeded in V1

## Model: `skills.PersonSkill`
Database table: `skills_personskill`

Purpose:
- Stores the narrow Skill assignment relationship between a `Person` and a canonical `Skill`.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `person` | `ForeignKey(people.Person)` | not null, `blank=False` | none | Required Person target |
| `skill` | `ForeignKey(skills.Skill)` | not null, `blank=False` | none | Required canonical Skill target |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |

### Constraints and Behavior
Current implementation:

- unique constraint on `person` + `skill`
- `person` uses `PROTECT`, so deleting a referenced Person is blocked
- `skill` uses `PROTECT`, so deleting a referenced Skill is blocked
- default ordering is `person_id`, `skill_id`, `id`

### Domain Rules
Currently implemented rules:

- a Person may have zero or many Skill assignments
- the same Skill may belong to many different People
- a Person must not receive the same Skill twice
- the join intentionally carries no proficiency, years, source, notes, or endorsement metadata
- deleting a `PersonSkill` does not delete the canonical `Skill`
- inactive `Skill` rows may continue to have stored `PersonSkill` references until explicitly removed

## Model: `interests.Interest`
Database table: `interests_interest`

Purpose:
- Stores the canonical Interest taxonomy reusable across person assignment, overview display, onboarding, and analytics.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `name` | `CharField(max_length=255)` | not null, `blank=False` | none | Required canonical label |
| `slug` | `SlugField` | not null, `blank=False` | none | Required unique machine-readable identifier |
| `description` | `TextField` | not null, `blank=True` | empty string | Optional description |
| `is_active` | `BooleanField` | not null | `True` | Inactive values remain stored but are excluded from normal active CRM reads |
| `display_order` | `PositiveIntegerField` | not null | `100` | Controls deterministic presentation order |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- `slug` is unique
- default ordering is `display_order`, `name`, `id`
- the approved initial 19-row taxonomy is seeded by migration
- canonical rows are updated by slug if they already exist
- unrelated rows are not deleted by the seed migration
- existing descriptions are preserved during canonical reseeding because no approved descriptions are seeded in V1

## Model: `interests.PersonInterest`
Database table: `interests_personinterest`

Purpose:
- Stores the narrow Interest assignment relationship between a `Person` and a canonical `Interest`.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `person` | `ForeignKey(people.Person)` | not null, `blank=False` | none | Required Person target |
| `interest` | `ForeignKey(interests.Interest)` | not null, `blank=False` | none | Required canonical Interest target |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |

### Constraints and Behavior
Current implementation:

- unique constraint on `person` + `interest`
- `person` uses `PROTECT`, so deleting a referenced Person is blocked
- `interest` uses `PROTECT`, so deleting a referenced Interest is blocked
- default ordering is `person_id`, `interest_id`, `id`

### Domain Rules
Currently implemented rules:

- a Person may have zero or many Interest assignments
- the same Interest may belong to many different People
- a Person must not receive the same Interest twice
- the join intentionally carries no direction, ranking, source, notes, willingness, availability, or commitment metadata
- inactive `Interest` rows may continue to have stored `PersonInterest` references until explicitly removed
- deleting a `PersonInterest` does not delete the canonical `Interest`

## Model: `tags.Tag`
Database table: `tags_tag`

Purpose:
- Stores the canonical internal CRM Tag taxonomy used for staff classification and relationship management.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `name` | `CharField(max_length=255)` | not null, `blank=False` | none | Required canonical label |
| `slug` | `SlugField` | not null, `blank=False` | none | Required unique stable identifier |
| `description` | `TextField` | not null, `blank=True` | empty string | Optional description |
| `is_active` | `BooleanField` | not null | `True` | Inactive values remain stored but are excluded from normal active CRM reads |
| `display_order` | `PositiveIntegerField` | not null | `100` | Controls deterministic presentation order |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- `slug` is unique
- default ordering is `display_order`, `name`, `id`
- the approved initial 9-row taxonomy is seeded by migration
- canonical rows are updated by slug if they already exist
- unrelated rows are not deleted by the seed migration
- existing descriptions are preserved during canonical reseeding because no approved descriptions are seeded in V1

## Model: `tags.PersonTag`
Database table: `tags_persontag`

Purpose:
- Stores the lifecycle-preserving internal CRM Tag assignment relationship between a `Person` and a canonical `Tag`.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `person` | `ForeignKey(people.Person)` | not null, `blank=False` | none | Required Person target |
| `tag` | `ForeignKey(tags.Tag)` | not null, `blank=False` | none | Required canonical Tag target |
| `is_active` | `BooleanField` | not null | `True` | Active lifecycle flag |
| `assigned_by` | `ForeignKey(accounts.User)` | not null, `blank=False` | none | Required assigning actor |
| `assigned_at` | `DateTimeField` | not null | `timezone.now` | Assignment timestamp |
| `removed_by` | `ForeignKey(accounts.User)` | `null=True`, `blank=True` | none | Required when inactive |
| `removed_at` | `DateTimeField` | `null=True`, `blank=True` | none | Required when inactive |

### Constraints and Behavior
Current implementation:

- unique constraint on `person` + `tag`
- `person` uses `PROTECT`, so deleting a referenced Person is blocked
- `tag` uses `PROTECT`, so deleting a referenced Tag is blocked
- `assigned_by` uses `PROTECT`, so deleting the assigning actor is blocked while referenced
- `removed_by` uses `PROTECT`, so deleting the removing actor is blocked while referenced
- default ordering is `person_id`, `tag_id`, `id`
- model validation enforces:
  - active assignments must have `removed_by` and `removed_at` unset
  - inactive assignments must have both `removed_by` and `removed_at` populated

### Domain Rules
Currently implemented rules:

- a Person may have zero or many Tag assignments
- the same Tag may belong to many different People
- a Person must not receive the same Tag twice
- PersonTag preserves assignment/removal lifecycle state rather than behaving like a disposable join row
- Tag assignment does not delete historical lifecycle state
- re-assignment reactivates the same row instead of creating a new row
- active rows require `removed_by = null` and `removed_at = null`
- inactive rows require both `removed_by` and `removed_at`
- normal CRM reads expose only active PersonTag assignments whose Tag definition is active

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
- current Django Admin lifecycle writes immutable audit history for successful grant, reactivation, and revocation

## Django-Managed Framework Tables
Current project features imply framework-managed tables such as:

- authentication and permissions tables from `django.contrib.auth`
- content type tables from `django.contrib.contenttypes`
- admin log tables from `django.contrib.admin`
- session tables from `django.contrib.sessions`

These exist to support Django's framework behavior and are separate from Elevate's domain model.

## Model: `audit.AuditEvent`
Database table: `audit_auditevent`

Purpose:
- Stores append-only cross-domain history that an important security or business event occurred.

Authoritative-boundary rules:

- domain models remain authoritative for current state
- `AuditEvent` is not a replacement for lifecycle fields such as `PersonTag`
- `AuditEvent` is not a generic log dump or revision engine
- historical audit rows are not fabricated for actions that occurred before audit capture was deployed

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `actor_user` | `ForeignKey(accounts.User)` | `null=True`, `blank=True` | none | Authenticated actor when known; may be null for unauthenticated events such as `LOGIN_FAILED` |
| `action` | `CharField(max_length=100)` | not null, `blank=False` | none | Controlled action identifier from Django `TextChoices` |
| `entity_type` | `CharField(max_length=100)` | not null, `blank=False` | none | Stable resource/domain label such as `User` or `Authentication` |
| `entity_id` | `CharField(max_length=255)` | `null=True`, `blank=True` | none | Generic identifier stored as string when present |
| `changes` | `JSONField` | not null, `blank=True` | `dict` | Small structured mutation summary; defaults to `{}` |
| `metadata` | `JSONField` | not null, `blank=True` | `dict` | Small non-secret contextual data; defaults to `{}` |
| `request_id` | `CharField(max_length=255)` | `null=True`, `blank=True` | none | Reserved for future trusted request correlation |
| `ip_address` | `GenericIPAddressField` | `null=True`, `blank=True` | none | Optional schema support only; current auth audit does not populate it |
| `occurred_at` | `DateTimeField` | not null | `timezone.now` | Event timestamp |

### Indexes and Ordering
Current implementation:

- default ordering is newest first: `occurred_at DESC`, then `id DESC`
- `action` is indexed
- `occurred_at` is indexed
- `(entity_type, entity_id)` has a composite index for future lookup use
- `actor_user` uses Django's normal foreign-key index

### Append-Only Protections
Current implementation:

- normal model `save()` permits initial insert only
- normal model updates are rejected after creation
- normal model `delete()` is rejected
- queryset `update()` is rejected
- queryset `delete()` is rejected
- Django Admin is read-only for inspection and does not allow add, edit, or delete

Application-level meaning:

- normal application workflows must only append new rows
- any future business mutation should write its audit row deliberately, not through magical implicit signals
- future business mutations should write audit in the same transaction as the authoritative mutation where practical
- required audit persistence failure rolls back the authoritative mutation rather than allowing state to commit without its audit record

### Security and Privacy Rules
Current implementation rules:

- generic audit payloads must never contain passwords, password hashes, session IDs, cookies, CSRF tokens, authorization headers, or reset tokens
- sensitive Note bodies must not later be copied into generic audit payloads
- failed login audit currently does not retain attempted email
- current auth audit also leaves `request_id` and `ip_address` null because no explicit trusted infrastructure/policy has been established yet

### Current Integrated Mutation Auditing
Current successful business audit capture:

- `MEMBERSHIP_CREATED` for successful Make Member
- `MEMBERSHIP_ENDED` for successful ACTIVE -> FORMER transitions
- `PROFESSIONAL_PROFILE_CREATED` for successful ProfessionalProfile creation
- `PROFESSIONAL_PROFILE_UPDATED` for successful ProfessionalProfile updates that persist at least one real business-field change
- `SKILL_ASSIGNED` for successful `PersonSkill` creation
- `SKILL_REMOVED` for successful `PersonSkill` deletion
- `INTEREST_ASSIGNED` for successful `PersonInterest` creation
- `INTEREST_REMOVED` for successful `PersonInterest` deletion
- `NOTE_CREATED` for successful `InternalNote` creation
- `NOTE_UPDATED` for successful `InternalNote` body edits that persist a real change
- `NOTE_ARCHIVED` for successful `InternalNote` archive
- `NOTE_RESTORED` for successful `InternalNote` restore
- `STAFF_ROLE_ASSIGNED` for successful new `StaffRoleAssignment` creation
- `STAFF_ROLE_REACTIVATED` for successful reuse of an existing revoked `StaffRoleAssignment` row
- `STAFF_ROLE_REVOKED` for successful `StaffRoleAssignment` revocation
- `TAG_ASSIGNED` for first-time active `PersonTag` creation
- `TAG_REACTIVATED` for reusing an existing inactive `PersonTag`
- `TAG_REMOVED` for active -> inactive `PersonTag` removal

Current conventions:

 - `Membership`, `ProfessionalProfile`, `PersonSkill`, `PersonInterest`, `InternalNote`, and `PersonTag` remain authoritative for current state
- `StaffRoleAssignment` also remains authoritative for current operational access state
 - `AuditEvent.entity_type` uses the mutated resource: `Membership`, `ProfessionalProfile`, `PersonSkill`, `PersonInterest`, `InternalNote`, `StaffRoleAssignment`, or `PersonTag`
- `AuditEvent.entity_id` stores that mutated row's primary key as a string
- `metadata.person_id` stores the related Person primary key as a string for future cross-domain Person audit history
- Skill lifecycle events also store `metadata.skill_id` as the canonical Skill primary key string
- Interest lifecycle events also store `metadata.interest_id` as the canonical Interest primary key string
- Internal Note lifecycle events store only compact note-state context such as `person_id` and body-changed markers
- Staff Access lifecycle events store `metadata.target_user_id` and `metadata.staff_role_id`, and may also include stable `metadata.staff_role_code`
- Tag lifecycle events also store `metadata.tag_id` as the canonical Tag primary key string
- ProfessionalProfile create events store only the persisted writable business fields using compact before/after values
- ProfessionalProfile update events store only fields that actually changed after persistence; no-op PATCH produces no update event
- Internal Note events must never store note body or archive_reason in `changes` or `metadata`
- Skill and Interest removal events may continue to reference a deleted historical relationship row through `AuditEvent.entity_id`
- Staff Access audit `actor_user` is the administrator performing the mutation, not the target user whose access changed
- `changes` remains compact and records only the business transition that occurred
- required audit persistence failure rolls back the same authoritative mutation transaction instead of silently dropping audit history
- no historical membership, professional profile, skill, interest, note, staff access, or tag events are fabricated for mutations that predated audit deployment

## Model: `notes.InternalNote`
Database table: `notes_internalnote`

Purpose:
- Stores sensitive internal staff-authored free-text context about a Person inside the CRM domain.

### Fields
| Field | Type | Null / Blank | Default / Automatic | Notes |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | not null | auto-created primary key | Django default primary key |
| `person` | `ForeignKey(people.Person)` | not null, `blank=False` | none | Required Person target |
| `body` | `TextField` | not null, `blank=False` | none | Required plain-text note body |
| `created_by` | `ForeignKey(accounts.User)` | not null, `blank=False` | none | Required original author |
| `archived_at` | `DateTimeField` | `null=True`, `blank=True` | none | Archive marker |
| `archived_by` | `ForeignKey(accounts.User)` | `null=True`, `blank=True` | none | Actor who archived the note |
| `archive_reason` | `TextField` | not null, `blank=True` | empty string | Optional archive explanation retained on InternalNote |
| `created_at` | `DateTimeField` | not null | `auto_now_add=True` | Set automatically on create |
| `updated_at` | `DateTimeField` | not null | `auto_now=True` | Updated automatically on save |

### Constraints and Behavior
Current implementation:

- default ordering is newest first: `created_at DESC`, then `id DESC`
- `person` uses `PROTECT`
- `created_by` uses `PROTECT`
- `archived_by` uses `PROTECT`
- body must not be blank or whitespace-only
- active note requires `archived_at = null` and `archived_by = null`
- active note also clears `archive_reason`
- archived note requires `archived_by`
- normal application hard-delete is blocked at model and queryset level
- no revision table exists in V1
