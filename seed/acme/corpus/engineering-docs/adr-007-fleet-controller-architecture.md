# ADR-007 Fleet Controller Architecture

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date accepted** | 2025-11-03 |
| **Deciders** | Wei Zhang (Staff Engineer), Marcus Bell (CTO) |
| **Component** | FleetOS – fleet controller (`fleetd`) |
| **Supersedes** | — |
| **Related** | ADR-004 Messaging Backbone, ADR-006 Robot Telemetry Schema |

## Context

FleetOS coordinates every AMR-200 and AMR-500 deployed at customer sites. It needs to know which robots are present, which are healthy, and which can take new missions. Until now, robot presence came from the mission scheduler indirectly. A robot was assumed to be alive as long as it kept acknowledging tasks. This caused three recurring problems:

1. **Slow failure detection.** An idle robot that lost power or Wi-Fi could stay "available" for minutes. The scheduler would then assign it a pick task that never started.
2. **Coupled responsibilities.** Presence tracking, mission dispatch and charging logic all lived in the same Python service. That made it hard to scale and hard to reason about.
3. **Uneven behaviour across sites.** Large sites running 60+ robots, mostly AMR-500 fleets, saw noticeably higher scheduling latency than small AMR-200 deployments.

The messaging backbone selected in ADR-004 is NATS. Robots already use it for telemetry, so a presence mechanism built on NATS needs no new infrastructure on the robot side.

Samuel Mensah's support team flagged "ghost robots" as one of the top sources of customer tickets in Q3 2025. That made a clean, explicit liveness model a priority.

## Decision

We will introduce a dedicated fleet controller service, **`fleetd`**, written in **Go**. It is the single source of truth for robot presence and state.

### Responsibilities of `fleetd`

- Maintain the authoritative registry of robots per site, including model, firmware version and last known pose.
- Track liveness using explicit heartbeats.
- Publish state-change events (`online`, `offline`, `degraded`) for the scheduler, the dashboard and alerting.
- Expose a read API used by the FleetOS web console.

### Heartbeat protocol

- Every robot publishes a heartbeat to `fleetd` **every 5 seconds** over **NATS**, on the subject `fleet.<site_id>.heartbeat.<robot_id>`.
- The heartbeat payload carries battery level, current mission ID (if any), firmware version and a monotonic sequence number.
- `fleetd` marks a robot **offline after 3 missed heartbeats (15 seconds)**. It then emits an `offline` event, and the scheduler immediately reassigns any pending tasks.
- When the next valid heartbeat arrives, the robot returns to `online` and the scheduler sees it as available again.

### Why Go

- Go's goroutines and channels handle thousands of per-robot timers cheaply.
- The official NATS Go client is mature and maintained.
- Go matches the language already used by the site gateway, which lets the platform team share libraries and on-call knowledge.

### Alternatives considered

| Option | Reason rejected |
|---|---|
| Extend the existing Python scheduler | Keeps the coupling problem and has weaker concurrency for per-robot timers |
| MQTT broker presence (last will) | Would add a second messaging system alongside NATS |
| Longer interval (10 s heartbeat) | Failure detection of 30 s judged too slow for dense pick zones |

## Consequences

### Positive

- A failed robot is detected within 15 seconds, which should sharply reduce "ghost robot" tickets.
- Scheduling and presence scale independently. `fleetd` can be sharded per site if needed.
- There is one clear owner of robot state, which simplifies debugging and dashboards.

### Negative / risks

- **Network blips.** Short Wi-Fi dropouts longer than 15 seconds will cause offline flapping. We will track the flap rate per site and revisit the threshold if it exceeds acceptable levels.
- **Message volume.** A 5-second interval adds steady NATS traffic. At a 200-robot site this is 40 messages per second, which is well within capacity, but it must be included in broker sizing.
- **Firmware dependency.** Robots need firmware that emits heartbeats. Older AMR-200 units require an update before they can be managed by `fleetd`.
- **New service to operate.** The platform team takes on on-call ownership of `fleetd`, including runbooks and alerting.

### Follow-up actions

- Wei Zhang: publish the heartbeat message schema in the telemetry repository.
- Platform team: add `fleetd` dashboards for heartbeat lag and offline events per site.
- Support (Samuel Mensah): update troubleshooting guides to reference `fleetd` offline events.