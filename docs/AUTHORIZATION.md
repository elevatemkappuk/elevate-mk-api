# Elevate MK Authorization
Status: Living Documentation
Last Updated: 2026-08-29

## Scope
This document describes the currently implemented staff authorization foundation for the Elevate MK Django API.

## Current Authorization Architecture
Authentication and staff authorization are separate concerns:

- `accounts.User` identifies the authenticated account
- `staff_access.StaffRole` defines canonical operational role codes
- `staff_access.StaffRoleAssignment` grants operational roles to users

Current backend rule:

- staff authorization is evaluated from active `StaffRoleAssignment` rows whose linked `StaffRole` is also active
- `Person.record_type` does not determine staff authorization

## Canonical V1 Roles
Currently implemented canonical roles:

- `CRM_ADMIN`
- `CRM_MANAGER`
- `CRM_VIEWER`

These are seeded deterministically by migration and are intended to be stable machine-readable backend role codes.

## Active Role Evaluation
A role code currently counts as active for a user only when all of the following are true:

- the user is authenticated
- the assignment row has `is_active=True`
- `revoked_at` is null
- the linked `StaffRole` has `is_active=True`

Returned role codes are sorted deterministically by `StaffRole.code`.

## Revocation and Reactivation
Current lifecycle behavior:

- revocation sets `is_active=False`
- revocation records `revoked_at`
- revocation may record `revoked_by`
- reactivation reuses the same assignment row
- reactivation clears revocation fields and restores `is_active=True`
- successful Django Admin grant, reactivation, and revocation also append immutable `AuditEvent` history rows
- `actor_user` in those audit rows is the administrator performing the mutation, not the target user receiving or losing access

No hard-delete lifecycle is implemented as the normal authorization path.

## 401 vs 403
Current permission semantics:

- anonymous user: `401 Unauthorized` when endpoint authentication is required
- authenticated user without a qualifying active staff role: `403 Forbidden`

## Permission Foundation
Currently implemented reusable backend helpers:

- `get_active_staff_role_codes_for_user(user)`
- `user_has_any_active_staff_role(user, allowed_role_codes=None)`
- `HasAnyActiveStaffRole`
- `HasActiveStaffRoleCodes`

These are intended to support future Staff CRM endpoints without introducing a large authorization framework.

Example future usage shape:

- any active staff role
- manager-or-admin role gate
- admin-only role gate

## /me/ Exposure
Current behavior:

- `GET /api/v1/auth/me/` includes `staff_roles`
- `staff_roles` contains only active operational staff role codes
- authenticated non-staff users receive `staff_roles: []`

## Backend Authoritativeness
Current rule:

- backend role evaluation is authoritative

Client-provided role claims are not part of the current architecture.

## Authorization and Person Classification
Current clarification:

- CRM role assignment does not determine `Person.record_type`
- a `CRM_ADMIN` user can be linked to either a `BUSINESS` or `TECHNICAL` person
- staff access remains evaluated from active `StaffRoleAssignment` data rather than person classification

## Currently Implemented vs Planned
Currently implemented:

- canonical staff role model
- user-role assignment model
- deterministic role seeding
- active-role evaluation helpers
- reusable DRF permission classes
- `/api/v1/auth/me/` role exposure
- Internal Notes are restricted to `CRM_ADMIN` and `CRM_MANAGER`
- `CRM_VIEWER` has no Internal Notes read or write access

## Django Admin and Audit Inspection
Current clarification:

- Django Admin technical privileges remain separate from Elevate CRM operational authorization
- read-only inspection of `audit.AuditEvent` in Django Admin is a technical administration capability, not a CRM role grant
- future CRM Audit History access should be implemented separately with explicit operational authorization rules
- Django `is_staff` and `is_superuser` still do not become Elevate CRM roles
- recording Staff Access audit history does not make Django `is_staff` or `is_superuser` an operational CRM role

Planned, not yet implemented:

- Staff CRM resource endpoints
- finer-grained operational permission policies beyond role-code checks
- additional non-staff authorization domains
