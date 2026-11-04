# Fleet API Reference v2

**Product:** FleetOS
**Audience:** Customer integration engineers (WMS, WES and ERP teams)
**Maintainer:** Platform Engineering (technical owner: Wei Zhang, Staff Engineer)
**Last reviewed:** 2026-11-02

## 1. Overview

The Fleet API v2 lets a warehouse's own systems read the state of their Acme Robotics fleet and send work to it. This covers AMR-200 and AMR-500 units managed through FleetOS. The API is REST over HTTPS. It accepts and returns JSON encoded in UTF-8. All timestamps are ISO 8601 in UTC.

**Base URL:** `https://api.fleetos.acme.test/v2`

Every path in this document is relative to the base URL.

## 2. Authentication

Clients authenticate with **OAuth 2.0 client credentials issued per customer**. Your Acme Robotics onboarding contact issues a `client_id` and `client_secret` to your organisation. A token can only reach robots in your own fleet.

### Requesting a token

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET
```

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIs...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

Send the token in the `Authorization` header on every request:

```
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
```

Keep the client secret on the server side. Never embed it in browser or handheld-scanner applications. To rotate credentials, open a request with Acme Robotics Support.

## 3. Rate limits

The rate limit is **600 requests per minute per API client**. Each response includes these headers:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | Requests allowed per minute (600) |
| `X-RateLimit-Remaining` | Requests left in the current window |
| `X-RateLimit-Reset` | Seconds until the window resets |

Requests over the limit return `429 Too Many Requests`. Wait for the time given in the `Retry-After` header, then back off exponentially if you keep receiving 429s. For dashboards that poll often, use one list call instead of many single-robot calls.

## 4. Endpoints

### 4.1 List robots

`GET /robots`

This returns every robot in your fleet, in pages. It accepts the optional query parameters `model` (`AMR-200` or `AMR-500`), `site_id`, `page` and `page_size` (maximum 100).

### 4.2 Get robot status

`GET /robots/{id}/status`

This returns the robot's **battery level, location and current task**.

```http
GET /robots/R-500-0142/status
Authorization: Bearer <token>
```

```json
{
  "robot_id": "R-500-0142",
  "model": "AMR-500",
  "battery_level": 78,
  "location": {
    "site_id": "RTM-DC1",
    "zone": "B4",
    "x": 412.6,
    "y": 88.1,
    "heading": 270
  },
  "current_task": {
    "task_id": "T-20261102-00931",
    "type": "transport",
    "state": "in_progress"
  },
  "reported_at": "2026-11-02T09:14:27Z"
}
```

`battery_level` is a percentage from 0 to 100. If the robot is idle, `current_task` is `null`.

### 4.3 Create a task

`POST /tasks`

```json
{
  "type": "transport",
  "pickup": { "site_id": "RTM-DC1", "station": "INBOUND-03" },
  "dropoff": { "site_id": "RTM-DC1", "station": "PACK-12" },
  "priority": "normal"
}
```

A successful request returns `201 Created` with a `task_id`. FleetOS chooses the robot unless the request includes a `robot_id`.

### 4.4 Get or cancel a task

- `GET /tasks/{task_id}` returns the task's state and the robot assigned to it.
- `DELETE /tasks/{task_id}` cancels the task if no robot has picked it up yet.

## 5. Errors

| Status | Meaning |
|---|---|
| `400` | Validation error; the `errors` array lists the fields |
| `401` | Token is missing or has expired |
| `403` | The robot or site is not in your fleet |
| `404` | The resource was not found |
| `429` | The rate limit was exceeded |
| `5xx` | FleetOS error; retry with backoff |

## 6. Support

For integration questions, contact Acme Robotics Support, led by Samuel Mensah. Include the `X-Request-Id` header value from the failing response in your ticket.