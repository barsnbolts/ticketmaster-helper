# ER Intelligence — forkable deployment

This folder is a self-contained static dashboard. The repository supplies the scheduled collector; Supabase stores history; Vercel serves the site.

## Why this route

- **GitHub**: source repository and a scheduled collector every five minutes.
- **Supabase Free**: PostgreSQL history and a public read-only API.
- **Vercel Hobby**: static hosting from the GitHub fork with no build process.
- No Lovable credits, browser-open requirement, Playwright installation, or Cloudflare connector.

The checked-in `config.js` points to the shared demonstration database so the interface has data immediately. Replace it with your own Supabase details for an independent deployment.

## 1. Fork the repository

Fork `barsnbolts/ticketmaster-helper`, then use the `er-intel-forkable` branch. After the template is merged to `main`, the branch selection is unnecessary.

## 2. Create the database

1. Create a free Supabase project.
2. Open **SQL Editor**.
3. Paste and run `forkable/supabase/schema.sql`.
4. Open **Project Settings → API** and copy:
   - Project URL
   - Publishable/anon key
   - Service-role key

The service-role key is secret. Never put it in `config.js` or browser code.

## 3. Connect the scheduled collector

In the forked GitHub repository, open **Settings → Secrets and variables → Actions** and create:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Then open **Actions → ER Intelligence Data Acquisition → Run workflow**. Confirm that the run succeeds and inserts three rows.

GitHub schedules the same workflow every five minutes. GitHub may delay scheduled jobs during busy periods, so the dashboard measures actual collection coverage rather than assuming perfect timing.

## 4. Point the dashboard at your database

Edit `forkable/config.js`:

```js
supabaseUrl: "https://YOUR_PROJECT.supabase.co",
supabaseAnonKey: "YOUR_PUBLIC_ANON_KEY",
```

The anon key is designed for browser use. The SQL schema exposes only read access to anonymous users.

## 5. Deploy on Vercel

1. Sign in to Vercel with GitHub.
2. Choose **Add New → Project** and import the fork.
3. Set **Root Directory** to `forkable`.
4. Set **Framework Preset** to `Other`.
5. Leave Build Command and Output Directory empty.
6. Deploy.

Vercel will create a stable `vercel.app` URL and redeploy automatically after pushes.

### Netlify alternative

Import the same GitHub fork, set the base/publish directory to `forkable`, and leave the build command empty. Vercel is recommended because its fork and static deployment flow is simpler for this project.

## Data design

The collector reads:

- THP's official Credit Valley JSON feed.
- Halton Healthcare's official emergency-department page for Milton and Oakville.

Every attempted reading records source time, retrieval time, HTTP status, response duration, parser version, payload hash, validity and validation flags. A unique database index removes repeated copies of the same source-published state.

The dashboard calculates, within a rolling 24-hour experiment window:

- Current estimates and patient counts.
- 15-minute, one-hour and three-hour movement.
- Source-specific freshness and confidence.
- Median, range, volatility and collection coverage.
- Lowest-estimate lead changes and time spent in the lead.
- Large movements, collection failures and synchronized events.
- A screenshot-ready 24-hour intelligence summary.

## Free-tier practicality

At three hospitals every five minutes, the database receives at most 864 attempted rows per day before deduplication. Compact rows remain far below Supabase's 500 MB free database limit for a substantial period. The scheduled writes also count as database activity, reducing the chance of the free project being paused for inactivity.

## Safety

This is an independent information experiment. Wait estimates are not total visit times, triage advice, or a measure of clinical capability. The sickest patients are treated first. Call 911 for a serious or life-threatening emergency.
