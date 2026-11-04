# Contractor Guide

Welcome to Acme Robotics. This guide covers what you need for your first week with us, whether you're working on the AMR-200, the AMR-500, or FleetOS. Your Acme sponsor (the employee who requested your engagement) is your first point of contact for questions about scope and priorities. Questions about access go to IT Administrator Erin Takahashi.

## Before Day One

Complete these steps before any technical access can be set up:

1. **Sign the NDA.** You must sign the NDA before you receive repository access. HR Manager Alice Moreau's team sends it through our e-signature tool. No exceptions are made, including for short engagements.
2. **Return your contractor agreement** and invoicing details. Acme invoices and pays in euros.
3. **Send your preferred username and public SSH key** to your sponsor, who forwards them to IT.

## Your First Week

| Day | What happens |
|-----|--------------|
| Monday | Welcome call with your sponsor. IT issues your VPN certificate and accounts. |
| Tuesday | Repository access is granted (only once your NDA is on file). Codebase walkthrough. |
| Wednesday | Development environment and simulator setup. First small task assigned. |
| Thursday | Architecture overview session. Ask your sponsor whether Staff Engineer Wei Zhang is running one this week. |
| Friday | Check-in with your sponsor on progress, blockers and next week's plan. |

If you are on site in Rotterdam, collect a visitor badge from reception each morning. Contractors are escorted in the robot test hall.

## Network Access

### WireGuard VPN

Contractors connect to Acme's internal network through the **WireGuard VPN**, using a certificate issued by IT.

- IT generates your certificate and sends the configuration through a secure channel. Never share it or copy it to another device.
- Install the official WireGuard client for your operating system and import the configuration.
- Each certificate is tied to one person and one device. If you change laptops, ask IT for a new certificate.
- Certificates are revoked when your engagement ends.

If you lose a device or suspect your certificate has been compromised, report it to IT immediately.

### Accounts You Will Receive

- Acme e-mail and chat account
- Access to the code repositories relevant to your engagement (after your NDA is signed)
- Issue tracker and internal documentation wiki
- Shared development and simulation environments

## What Contractors Cannot Access

These limits apply to every contractor, regardless of role or seniority:

- **Customer data.** Contractors never get access to customer data. This includes warehouse layouts, FleetOS customer telemetry, support tickets and customer contact details.
- **Production systems.** Contractors never get access to production systems. This covers production FleetOS environments and the deployment pipelines that push to customer sites.

If a task appears to need either of these, stop and tell your sponsor. An Acme employee will handle that part of the work or provide anonymised or synthetic data instead. Please don't ask colleagues to share screenshots, exports or credentials as a workaround.

## Working Practices

- **Code:** Work on feature branches and open pull requests. An Acme engineer reviews and merges all changes.
- **Hardware:** Physical AMR-200 and AMR-500 units are used only under supervision of Operations staff. Use the simulator for remote work.
- **Communication:** Use your Acme chat account for project discussion, not personal messaging apps.
- **Dates:** Write dates in ISO format (for example, 2026-11-09) in tickets, documents and commit messages.
- **Confidentiality:** Your NDA covers source code, roadmap details and anything you see in meetings or on site.

## Who to Contact

| Topic | Contact |
|-------|---------|
| Scope, priorities, daily work | Your Acme sponsor |
| VPN certificate, accounts, devices | Erin Takahashi, IT Administrator |
| NDA, contracts, on-site logistics | Alice Moreau, HR Manager |
| Engineering standards and architecture | Wei Zhang, Staff Engineer |

## End of Engagement

On your last day, IT revokes your VPN certificate and disables your accounts. Delete any local copies of Acme code and documents, and return any loaned equipment or badges to your sponsor.