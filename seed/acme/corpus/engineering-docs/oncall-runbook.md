# On-call Runbook

**Owner:** FleetOS Engineering
**Applies to:** All engineers on the FleetOS on-call rotation
**Last reviewed:** 2026-11-02

## 1. Purpose

This runbook describes how FleetOS engineers handle production incidents while on call. FleetOS is the subscription platform that our customers use to coordinate and monitor their AMR-200 and AMR-500 fleets. An outage in FleetOS can stop robots mid-shift in a customer warehouse, so speed and clear communication matter more than a perfect diagnosis.

## 2. Rotation

- The on-call rotation changes **every Monday at 09:00 CET**.
- The outgoing engineer hands over to the incoming engineer before the change. They review open incidents, recent deploys and any alerts that are still noisy.
- If you need to swap a shift, arrange cover with a teammate and update the rotation schedule before the shift starts. Verbal agreements are not enough.
- While on call, keep your laptop and phone within reach. You should be able to acknowledge a page within 5 minutes.

## 3. When You Get Paged

1. **Acknowledge** the page so the team knows someone is working on it.
2. **Assess impact.** Answer three questions:
   - Which customers are affected?
   - Are robots stopped, degraded or unaffected?
   - Is the problem spreading?
3. **Open an incident channel** and post a one-line summary. Example: "FleetOS task dispatch failing for two EU sites, robots idle."
4. **Mitigate first, investigate later.** Restore service using the known fixes below before you look for a root cause.
5. **Inform Support.** Tell Support Lead Samuel Mensah's team so they can update customers who call in.

## 4. Common Mitigations

### 4.1 Restarting the fleet controller

The fleet controller (`fleetd`) coordinates task assignment and traffic management for connected robots. If it hangs, robots stop receiving new tasks.

To restart it, run this on the **controller host**:

```bash
systemctl restart fleetd
```

After the restart:

- Run `systemctl status fleetd` to confirm the service reports `active (running)`.
- Watch the dashboard to confirm robots reconnect and the task queue starts draining.
- If `fleetd` fails again within a few minutes, do not keep restarting it. Escalate (see section 5).

### 4.2 Rolling back a recent deploy

If the incident started shortly after a release, rolling back is usually the fastest fix. Roll back to the last known good version, then confirm that error rates return to normal.

### 4.3 Robots offline at a single site

If only one customer site is affected, the cause is often local, such as site Wi-Fi or a network change. Confirm that FleetOS is healthy for other sites, then work with Support to contact the customer.

## 5. Escalation

| Condition | Action |
|---|---|
| Incident is not mitigated within **15 minutes** | Page the Staff Engineer, **Wei Zhang** |
| Multiple customers have robots stopped | Notify CTO Marcus Bell |
| Possible data exposure or security issue | Notify IT Administrator Erin Takahashi immediately |
| Hardware fault suspected on AMR-200 or AMR-500 units | Notify Head of Operations Olivia Grant |

Do not wait until the 15-minute mark if you already know you are stuck. Escalating early is always acceptable.

## 6. Communication Guidelines

- Post an update in the incident channel at least every 15 minutes, even if nothing has changed.
- Use plain language: what is broken, who is affected and what you are doing about it.
- Never share internal hostnames or logs directly with customers. Route all customer communication through Support.

## 7. After the Incident

1. Confirm the service is stable and close the incident channel with a short summary.
2. Write a post-incident review within 3 working days. Include:
   - a timeline
   - the root cause, if known
   - follow-up actions with owners
3. Add any new mitigation steps to this runbook.

## 8. Quick Reference

- **Restart fleet controller:** `systemctl restart fleetd` on the controller host
- **Escalation threshold:** 15 minutes without mitigation → page Wei Zhang
- **Rotation change:** every Monday at 09:00 CET