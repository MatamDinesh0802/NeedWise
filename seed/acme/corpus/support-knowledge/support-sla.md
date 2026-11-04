# Customer Support SLA

| Field | Value |
|---|---|
| Document owner | Samuel Mensah, Support Lead |
| Approved by | Olivia Grant, Head of Operations |
| Effective date | 2026-11-02 |
| Applies to | AMR-200, AMR-500 and FleetOS customers with an active support contract |

## 1. Purpose

This document sets out the service levels Acme Robotics commits to when customers contact Customer Support. It covers how quickly we respond, how tickets are prioritised and how issues are escalated. Account managers in Diego Alvarez's sales team use this SLA when onboarding new customers. Support staff should treat it as the reference for day-to-day ticket handling.

## 2. Support Channels

- **Customer portal:** the preferred channel for all non-urgent requests.
- **E-mail:** messages sent to support create a ticket automatically.
- **Phone hotline:** reserved for Priority 1 incidents and staffed around the clock.

Business hours are Monday to Friday, 08:00 to 18:00 CET, excluding Dutch public holidays.

## 3. Priority Levels

| Priority | Definition | Examples |
|---|---|---|
| P1 – Critical | Operations stopped or a safety risk to people or goods | Fleet-wide FleetOS outage; AMR-500 failing to stop at an obstacle |
| P2 – High | Major function degraded, workaround available | Several AMR-200 units dropping off the map; route planning slowed significantly |
| P3 – Standard | Single-unit issue or non-blocking defect | One robot with repeated charging errors; dashboard report not loading |
| P4 – Request | Questions, configuration changes, feature requests | New user accounts; zone layout changes |

The customer proposes a priority when opening a ticket. The assigned engineer confirms it, or adjusts it with a short explanation to the customer.

## 4. Response Targets

| Priority | First response | Coverage |
|---|---|---|
| P1 – Critical | Within 1 hour | 24/7 |
| P2 – High | Within 2 business hours | Business hours |
| P3 – Standard | Within 4 business hours | Business hours |
| P4 – Request | Within 4 business hours | Business hours |

**Standard tickets** receive a first response within **4 business hours**. **Priority 1 incidents** receive a response within **1 hour, 24/7**, including weekends and public holidays.

A "first response" means a named engineer has acknowledged the ticket and either started diagnosis or asked for the specific information needed. Automated acknowledgements do not count.

## 5. Updates During an Incident

- **P1:** status updates at least every 2 hours until service is restored. After that, a written incident summary follows within 5 business days.
- **P2:** updates at least once per business day.
- **P3 and P4:** updates whenever there is material progress, and at least weekly.

## 6. Escalation Path

1. **On-call support engineer:** handles the first response and initial diagnosis.
2. **Support Lead (Samuel Mensah):** brought in automatically if a P1 is not stabilised within 4 hours, or if any SLA target is at risk.
3. **Engineering:** hardware and firmware issues go to the on-call robotics engineer. FleetOS platform issues are escalated with Staff Engineer Wei Zhang as technical point of contact.
4. **Executive:** any P1 lasting more than 24 hours is reported to the CTO, Marcus Bell, and the Head of Operations, Olivia Grant.

## 7. Customer Responsibilities

To help us meet these targets, customers are asked to:

- Provide robot serial numbers, FleetOS site ID and a contact who can reach the site.
- Allow remote diagnostic access to FleetOS where needed.
- Use the phone hotline for P1 incidents rather than e-mail only.

## 8. Exclusions

Response targets do not apply to:

- Issues caused by unauthorised hardware modifications.
- Robots running firmware more than two major versions behind the current release.
- Scheduled maintenance windows announced at least 5 business days in advance.

## 9. Reporting and Review

Support publishes monthly SLA attainment figures in the internal operations dashboard. Customers can request a quarterly summary from their account manager. The Support Lead and Head of Operations review this SLA once a year, or sooner if a pattern of missed targets emerges.