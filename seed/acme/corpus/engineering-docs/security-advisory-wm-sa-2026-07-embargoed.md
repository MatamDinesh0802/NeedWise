# Security Advisory WM-SA-2026-07 (embargoed)

Status: embargoed until the fix ships. Shared with the people fixing it only: do not forward,
do not discuss in public channels. Reference: CANARY-DOCACL-7Z4.

## Summary

The FleetOS maintenance API (port 8443 on every fleet controller) accepts a maintenance session
token after the session that issued it has ended. Someone who captured a token on the warehouse
network could keep calling maintenance endpoints for up to 72 hours. Severity: high.

## Affected versions

- FleetOS 2.7.0 to 2.9.3, on fleet controllers with remote maintenance switched on.
- FleetOS 3.0 release candidates before rc4.

## Fix

- FleetOS 2.9.4 ends maintenance tokens together with their session and shortens their lifetime
  to 15 minutes. Planned release: 2026-11-24.
- Until then, switch remote maintenance off on controllers that do not need it, and restrict
  port 8443 to the maintenance VLAN.

## Disclosure

Customers are told on 2026-11-25, the day after the fix, through the support portal. Until then
only Engineering leadership and the two engineers on the fix know the details: Wei Zhang owns
the patch, Fatima Hassan the network guidance.
