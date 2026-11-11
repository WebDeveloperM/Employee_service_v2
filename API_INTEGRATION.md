# Employee Service API Integration Guide

This document is intended for other backends and frontends that need to reuse `employee_service` as an external API.

External integration is read-only. When a request is authenticated with `X-Employee-Service-Key` or `Authorization: Bearer <key>`, the service only allows `GET`, `HEAD`, and `OPTIONS` methods.

## Documentation URLs

- Swagger UI: `/api/docs/`
- ReDoc: `/api/redoc/`
- OpenAPI schema: `/api/schema/`
- API base path: `/api/v1/`

Example production-style base URL:

```text
https://192.168.2.72:8010/api/v1
```

## Authentication

All reusable API endpoints require authentication unless explicitly stated otherwise.

Supported formats:

```http
X-Employee-Service-Key: dev-employee-service-key
```

or:

```http
Authorization: Bearer dev-employee-service-key
```

Configured keys are read from `EMPLOYEE_SERVICE_API_KEYS`.

## Request Formats

- This external contract is read-only and returns employee, department, and section data.
- Write operations are intentionally blocked for service API keys.

## Pagination and Filters

List endpoints use standard DRF pagination by default.

Employee list supports:

- `source_system`
- `tabel_number`
- `external_id`
- `external_ids` as comma-separated values
- `slugs` as comma-separated values
- `search`
- `no_pagination=true`

Example:

```text
GET /api/v1/employees/?source_system=tb-project&external_ids=101,102,103&no_pagination=true
```

## Main Endpoints

### Departments

- `GET /departments/` list departments
- `GET /departments/{id}/` retrieve department

Department payload:

```json
{
  "name": "Цех 1",
  "boss_full_name": "Ali Valiyev"
}
```

### Sections

- `GET /sections/`
- `GET /sections/{id}/`

Section payload:

```json
{
  "name": "Участок А",
  "department_id": 1
}
```

### Employees

- `GET /employees/`
- `GET /employees/{slug}/`

Important employee fields:

- `source_system`
- `external_id`
- `first_name`
- `last_name`
- `surname`
- `tabel_number`
- `gender`
- `date_of_birth`
- `login`
- `password`
- `password_confirm`
- `role`
- `passport_series`
- `passport_number`
- `jshshir`
- `department_id`
- `section_id`
- `department_name`
- `section_name`
- `boss_full_name`
- `base_image`
- `requires_face_id_checkout`
- `metadata`

Notes:

- `slug` is generated automatically and is used in detail routes.
- `source_system + external_id` is unique when `external_id` is not empty.
- External callers cannot create, update, delete, verify, or synchronize employees through the service API key.

## Common Error Responses

Examples:

```json
{
  "error": "Сотрудник не найден"
}
```

```json
{
  "error": "Требуется поле captured_image"
}
```

```json
{
  "detail": "Недействительный API-ключ сервиса сотрудников."
}
```

## Recommended Integration Pattern

For another project that needs employee data and face verification:

1. Store `EMPLOYEE_SERVICE_BASE_URL` and `EMPLOYEE_SERVICE_API_KEY` in environment.
2. Use `GET /employees/` with `source_system`, `external_id`, `external_ids`, `slugs`, or `tabel_number` filters for lookups.
3. Use `GET /employees/{slug}/` when the UI needs a full employee card.
4. Use `GET /departments/` and `GET /sections/` for reference catalogs.
5. Keep Swagger or the exported OpenAPI schema as the contract source.
6. Do not rely on this integration for write operations; they are blocked for service API keys.

## cURL Examples

List employees:

```bash
curl -X GET "https://192.168.2.72:8010/api/v1/employees/?source_system=tb-project&no_pagination=true" \
  -H "X-Employee-Service-Key: dev-employee-service-key"
```

Get employee details:

```bash
curl -X GET "https://192.168.2.72:8010/api/v1/employees/tb-project-tb-1001-ali-valiyev/" \
  -H "X-Employee-Service-Key: dev-employee-service-key"
```

List departments:

```bash
curl -X GET "https://192.168.2.72:8010/api/v1/departments/" \
  -H "X-Employee-Service-Key: dev-employee-service-key"
```