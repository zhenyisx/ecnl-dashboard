# ECNL Girls Conference Standings Dashboard

A live standings dashboard for ECNL (Elite Clubs National League) Girls conferences, built as a single-page HTML app backed by a lightweight Python proxy server.

## Features

- **Conference Standings** — Browse all 10 ECNL conferences across 5 seasons (2021-22 through 2025-26), with per-flight standings tables
- **Playoffs & Finals** — Group stage standings for the national Playoffs event, with links to schedules/standings; bracket view opens on TGS
- **★ My Teams** — Star any team to track them in a personal favorites list
- **Age group navigation** — Tabs dynamically populated from the API; keyboard arrow-key navigation supported
- **Dark mode** — Toggle between light and dark themes
- **Caching** — API responses cached in localStorage; manual refresh button per view
- **Deep links** — Season, age group, and conference encoded in the URL hash

## Setup

### 1. Start the proxy server

```bash
python proxy_server.py
```

This starts a local server on **port 5000** that proxies API calls to `https://api.athleteone.com`.

### 2. Open the dashboard

Navigate to [http://localhost:5000](http://localhost:5000) in your browser.

## Data Sources

- **API**: `https://api.athleteone.com` (TGS / AthleteOne)
- **Standings/Schedules UI**: `https://public.totalglobalsports.com`

## Season Coverage

| Season  | Conferences | Playoffs | Finals |
|---------|-------------|----------|--------|
| 2025-26 | ✅ 10        | —        | —      |
| 2024-25 | ✅ 10        | ✅ event 3865 | ✅ event 3975 |
| 2023-24 | ✅ 10        | —        | —      |
| 2022-23 | ✅ 10        | —        | —      |
| 2021-22 | ✅ 9 (no NorCal) | —   | —      |
