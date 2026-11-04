# Known Issue - AMR-200 Battery Degradation

**Article ID:** KB-AMR200-0147
**Product:** AMR-200
**Category:** Hardware / Power System
**Status:** Known Issue – Mitigation Available
**Owner:** Samuel Mensah, Support Lead
**Last updated:** 2026-11-03

---

## Summary

AMR-200 robots can show faster-than-expected battery degradation after about 18 months in service. In Q3 2026 this was the top complaint category in Support. The degradation shows up as shorter runtime per charge, more frequent return-to-dock events and, in some units, unexpected low-battery shutdowns during long missions.

Firmware 4.2.1 reduces charging current, which slows the degradation. Batteries below 70 percent health are replaced free of charge under warranty.

This issue does not affect the AMR-500.

## Symptoms

Customers usually report one or more of the following:

- Runtime per full charge drops noticeably compared with the first year of operation.
- FleetOS shows a robot docking more often than others on the same route profile.
- A robot reports low battery and aborts a mission shortly after leaving the dock.
- The battery health value in FleetOS (**Robot > Diagnostics > Power**) declines steadily month over month.

Symptoms are most common on robots that run multi-shift schedules with frequent opportunity charging.

## Affected Configurations

| Item | Details |
|---|---|
| Robot model | AMR-200 |
| Typical onset | About 18 months in service |
| Firmware | Versions earlier than 4.2.1 |
| Higher-risk usage | Multi-shift operation, frequent short top-up charges |

## Cause

Engineering's analysis found that the charging current used by earlier firmware puts extra stress on the battery cells over time. The effect builds up slowly, so it usually appears only after about 18 months of operation.

## Resolution

### Step 1: Check battery health

1. In FleetOS, open **Robot > Diagnostics > Power** for the affected robot.
2. Note the **Battery Health (%)** value.
3. If the robot is not reporting to FleetOS, connect it to the dock and run a diagnostics refresh before continuing.

### Step 2: Update firmware to 4.2.1

Install firmware 4.2.1 on all AMR-200 robots in the fleet, not only the ones showing symptoms. The update reduces charging current and slows further degradation.

1. In FleetOS, go to **Fleet > Firmware**.
2. Select the AMR-200 robots to update and choose version **4.2.1**.
3. Schedule the update during a low-activity window. Each robot must be docked during installation.
4. Confirm the version under **Robot > About** once the update completes.

**Note:** Firmware 4.2.1 slows degradation but does not restore capacity that has already been lost.

### Step 3: Warranty replacement (if health is below 70 percent)

Batteries below 70 percent health are replaced free of charge under warranty. To request a replacement:

1. Export the diagnostics report from **Robot > Diagnostics > Export**.
2. Open a support ticket with the category **Battery Replacement – Warranty** and attach the report.
3. Include the robot serial number and the site address for shipping.

Support will confirm eligibility and arrange shipment of the replacement battery.

If health is 70 percent or above, keep monitoring after the firmware update and contact Support if it drops below the threshold.

## Guidance for Support Agents

- Tag related tickets with `amr200-battery` so Q4 trends can be tracked against the Q3 2026 baseline.
- Always confirm the firmware version before escalating. Most new reports come from robots that are not yet on 4.2.1.
- Escalate to Engineering only if a robot on firmware 4.2.1 shows sudden degradation, such as a health drop of several points within a single week.

## Related Articles

- KB-FOS-0032 – Updating Robot Firmware via FleetOS
- KB-AMR200-0098 – Docking Station Troubleshooting
- KB-GEN-0011 – Warranty Coverage Overview