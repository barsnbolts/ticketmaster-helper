# ER Wait Live

Public, mobile-first emergency department wait-time dashboard for:

- Credit Valley Hospital
- Milton District Hospital
- Oakville Trafalgar Memorial Hospital

The site is generated from official hospital sources by a scheduled GitHub Actions collector. Official sources are always preferred; ERstat is used only as an explicitly labelled fallback. The dashboard rejects the known stale January 23, 2025 THP zero-value placeholder.

## Refresh model

- GitHub Actions checks sources every five minutes, the shortest supported scheduled interval.
- Safari polls the deployed JSON every 60 seconds and updates without a page reload.
- Hospital source timestamps and collection timestamps are stored separately.
- Failed source checks preserve the last valid value and visibly mark delayed or stale data.

## Safety

Displayed times are estimates only. Emergency departments triage by clinical severity. Call 911 for a serious or life-threatening emergency and do not delay care based on a displayed wait time.

## Design

The interface follows the open-source UI UX Pro Max guidance: accessible contrast, semantic status labels, visible keyboard focus, 44px touch targets, tabular numerals, reduced-motion support, responsive mobile layout, and clear source provenance.
