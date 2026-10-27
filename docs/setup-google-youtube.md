# Setup: Google Cloud, YouTube APIs and CTR history

One-time manual setup for the owner's channel. Steps 1–5 can be done **now**, before any code exists. Step 4 in particular is time-critical, because Reporting API history only starts when the job is created.

## 1. Google Cloud project
1. Go to <https://console.cloud.google.com/> → create a project, e.g. `yta-prod`.
2. *APIs & Services → Library* → enable:
   - **YouTube Data API v3**
   - **YouTube Analytics API**
   - **YouTube Reporting API**

## 2. OAuth consent screen (Google Auth Platform)
1. *Branding*: app name `YouTube Analyser`, support e-mail = your address.
2. *Audience*: user type **External** (required for a normal Gmail account).
3. *Data Access* → add scopes:
   - `https://www.googleapis.com/auth/youtube.readonly`
   - `https://www.googleapis.com/auth/yt-analytics.readonly`
4. *Audience* → **Publish app** → status **In production**.
   - Without this, refresh tokens expire after 7 days and the daily job breaks every week.
   - Verification is **not** required for personal use. During consent you will see "Google hasn't verified this app" → *Advanced → Go to YouTube Analyser*. This is expected.

## 3. OAuth client
1. *Clients → Create client* → type **Desktop app**, name `yta-cli`.
2. Download the JSON. **Never commit it.** After the Terraform foundation exists, store it with:
   ```sh
   aws ssm put-parameter --profile yta-admin --region eu-central-1 \
     --name /yta/oauth/client --type SecureString \
     --value file://client_secret.json
   rm client_secret.json
   ```

## 4. Create Reporting API jobs now (impressions / CTR)
A job generates daily reports from its creation onward, plus **30 days of history before creation**. Each report can be downloaded for **60 days**. If you create the jobs today, ingestion only has to ship within ~60 days and no data is lost.

Quickest way, without code: use the API Explorer.
1. Open <https://developers.google.com/youtube/reporting/v1/reference/rest/v1/jobs/create> → *Try this method*.
2. Sign in and choose **your channel** (Brand Account if applicable).
3. Request body (run twice):
   ```json
   { "reportTypeId": "channel_reach_basic_a1",    "name": "yta-reach-basic" }
   { "reportTypeId": "channel_reach_combined_a1", "name": "yta-reach-combined" }
   ```
4. Save the returned job `id`s. Later, `yta onboard` lists existing jobs with `jobs.list` and reuses them. If a job created via the API Explorer is not visible to the project's own OAuth client, it can be registered by ID (`yta onboard --reporting-job <id>`). *(To verify during phase 2.)*

## 5. Fill CTR history manually (YouTube Studio export)
For everything older than ~30 days before step 4. YouTube Studio → *Analyse* → *Erweiterter Modus*, period *Gesamte Laufzeit*. Then *Aktuelle Ansicht exportieren* → *Kommagetrennte Werte (.csv)*. German UI is supported (formats in `SPEC.md` §9.1).

| # | Dimension tab | Table columns | Chart metric / selection | Status |
|---|---|---|---|---|
| A | *Videos* | Aufrufe, Wiedergabezeit, Abonnenten, Durchschnittl. Wiedergabedauer, **Thumbnail-Impressionen**, **Thumbnail-Klickrate** | — (chart hidden) | ✅ provided (lifetime totals per video) |
| A-monthly | *Videos* | same as A | — | optional: repeat A with the date range set to **one calendar month** per export → per-video CTR per month (importer takes the period from the folder name) |
| B | *Datum* | all available metrics incl. **Thumbnail-Impressionen**, **Thumbnail-Klickrate** | — | ✅ provided (`…-3`, channel-level daily, 27 metrics) |
| C / D | *Videos* | any | chart on, metric **Thumbnail-Impressionen** (C) / **Thumbnail-Klickrate** (D), all videos ticked | optional: per-video daily CTR, the most effort |

Decision for v1: **A + B are sufficient.** Daily reach data comes from the Reporting API from job creation onward; B–D only add finer-grained history.

Store the unzipped folders under `data/`, which is git-ignored and never committed. They are imported with `yta import studio …` once phase 7 is implemented.

## 6. AWS (handled by the Terraform foundation change)
- IAM Identity Center permission sets (proposed):
  - `yta-admin` — Terraform and administration
  - `yta-operator` — CLI write access: onboarding, import, purge
  - `yta-readonly` — MCP server: read-only access to `curated/`
- Local profiles via `aws configure sso`. No long-lived access keys.
