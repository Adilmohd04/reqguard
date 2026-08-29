# Tenant Isolation Requirements

REQ-04: Data access must be strictly isolated to the requesting tenant. Cross-tenant record queries must raise TenantAccessDeniedError.
