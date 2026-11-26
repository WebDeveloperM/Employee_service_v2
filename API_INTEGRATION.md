# Employee Service API Integration Guide

This document is intended for other backends and frontends that need to reuse `employee_service` as an external API.

External integration is read-only. When a request is authenticated with `X-Employee-Service-Key` or `Authorization: Bearer <key>`, the service only allows `GET`, `HEAD`, and `OPTIONS` methods.

## Timekeeper API (timekeepers are assigned by another system)

The department timekeeper is assigned and removed by **another system** through `PUT`/`DELETE /api/v1/timekeeper-assignments/{department_id}/` (there is no assignment page in employee_service). The same system then reads and updates the employees of **one department** on behalf of its timekeeper through `/api/v1/timekeeper/{timekeeper_tabel}/…`. Both groups work **only with the service key** (no per-timekeeper login or token). Timekeepers never sign in to employee_service: there is no «Табелчи» role or timekeeper page here (removed 2026-11-26), the other system authenticates its users itself (E-IMZO), and an assignment does not depend on or change any employee_service account. Full description: [`API_TIMEKEEPER.md`](API_TIMEKEEPER.md) (Uzbek); Swagger tags `timekeeper-assignments` and `timekeeper`.

## Documentation URLs

- Swagger UI: `/api/docs/`
- ReDoc: `/api/redoc/`
- OpenAPI schema: `/api/schema/`
- API base path: `/api/v1/`

Example production-style base URL:

```text
https://192.168.101.6:8010/api/v1
```

## Authentication

All reusable API endpoints require authentication unless explicitly stated otherwise.

Supported formats:

```http
X-Employee-Service-Key: <EMPLOYEE_SERVICE_API_KEY>
```

or:

```http
Authorization: Bearer <EMPLOYEE_SERVICE_API_KEY>
```

Configured keys are read from `EMPLOYEE_SERVICE_API_KEYS`.

- Production keys must be long random values (>= 32 characters); the service refuses to start with a placeholder key when `EMPLOYEE_SERVICE_DEBUG=false`.
- Employee photos (`base_image_url`, `/media/...`) are public by default (`MEDIA_REQUIRE_AUTH=false`): the TB project (ppe_product) shows them in the browser by direct URL and its media proxy downloads them without the key. With `MEDIA_REQUIRE_AUTH=true` only logged-in admin/HR users, the photo's owner and requests with the service key header get them.
- The service key can also write on `/api/v1/`: TB uses it for `POST /employees/upsert/` (with `base_image`), `PATCH /employees/{slug}/`, department/section create/update/delete, `PATCH /employees/{slug}/base-image/`, `PATCH /employees/{slug}/face-id-exemption/`, `DELETE /employees/base-image-change-logs/{id}/` and the face verification `POST`s. Treat the key like a password: whoever has it can change employee data.

## Request Formats

- The read endpoints below return employee, department, and section data. TB also writes through `/api/v1/` with the service key (see Authentication).
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
curl -X GET "https://192.168.101.6:8010/api/v1/employees/?source_system=tb-project&no_pagination=true" \
  -H "X-Employee-Service-Key: <EMPLOYEE_SERVICE_API_KEY>"
```

Get employee details:

```bash
curl -X GET "https://192.168.101.6:8010/api/v1/employees/tb-project-tb-1001-ali-valiyev/" \
  -H "X-Employee-Service-Key: <EMPLOYEE_SERVICE_API_KEY>"
```

List departments:

```bash
curl -X GET "https://192.168.101.6:8010/api/v1/departments/" \
  -H "X-Employee-Service-Key: <EMPLOYEE_SERVICE_API_KEY>"
```