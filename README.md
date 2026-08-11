# Personal Expense Tracker (Streamlit)

A single running app you use all year — no new file needed each month. Data is
stored via SQLAlchemy, which works with either:
- a local SQLite file (`expenses.db`, created automatically) for local use, or
- an external Postgres database (via a `DATABASE_URL` environment variable) for
  cloud hosting where the filesystem doesn't persist — like Render's free tier.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens in your browser at `http://localhost:8501`, using a local `expenses.db`
SQLite file. This is fine for local use since the file just sits on your disk.

## Deploy on Render (free tier)

Render's free web services have an **ephemeral filesystem** — anything written to
disk (like a SQLite file) is wiped on every restart or redeploy, and free instances
also spin down after ~15 minutes of inactivity, which triggers a restart. So for
Render you need an external database that lives outside Render's filesystem. Any
free-tier Postgres works; two easy options:

- **Neon** — https://neon.tech (free tier, no card required for the hobby plan)
- **Supabase** — https://supabase.com (free tier)

### Steps

1. **Create a free Postgres database** on Neon or Supabase. Copy the connection
   string it gives you — it looks like:
   `postgresql://user:password@host/dbname?sslmode=require`

2. **Push this project to a GitHub repo** (`app.py`, `requirements.txt`,
   `render.yaml`, `.streamlit/config.toml` included here).

3. **Create a new Web Service on Render** (https://dashboard.render.com):
   - Connect your GitHub repo. Render will detect `render.yaml` and pre-fill the
     build/start commands — or set them manually:
     - Build command: `pip install -r requirements.txt`
     - Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
   - Plan: **Free**

4. **Set the environment variable** on the Render service:
   - Key: `DATABASE_URL`
   - Value: the Postgres connection string from step 1

5. **Deploy.** Once it's live, the sidebar of the app will show
   "🟢 Connected to external Postgres database" — confirming your data will
   persist across restarts and redeploys.

If you ever see "🟡 Using local SQLite" in the sidebar while running on Render, it
means `DATABASE_URL` isn't set correctly — data entered in that state will be lost
on the next restart, so fix the env var before relying on it.

### Notes on Render's free tier

- The service spins down after inactivity and takes ~30–50 seconds to wake up on
  the next visit — normal for the free plan, and harmless now that data lives in
  Postgres rather than on Render's disk.
- Free Postgres plans (Neon/Supabase) also pause after inactivity but wake up
  automatically on the next query, usually within a few seconds.

## How it's organized

- **➕ Add Expense** — daily entry form (Date, Day auto-filled, Category, Sub-Category,
  Description, Payment Method, Amount, Need/Want, Recurring/One-Time, Person, Notes).
  Fixed vs Variable is derived automatically from the category.
- **📅 Daily Dashboard** — total for the day, transaction count, highest expense,
  category split, Need vs Want, Recurring vs One-Time.
- **🗓️ Monthly Dashboard** — total, average daily, category %, highest category/day,
  month-over-month change, budget vs actual, remaining budget, savings, Fixed/Variable,
  Need/Want, Recurring/One-Time.
- **📆 Yearly Dashboard** — annual total, monthly averages, YTD, projected annual spend,
  highest/lowest month, annual budget vs actual, month-over-month growth chart.
- **📊 Category Analysis** — top 10 categories, category→sub-category treemap, full
  summary table for any custom date range.
- **💵 Budget & Income Settings** — set monthly income and per-category budgets; these
  feed every Budget vs Actual and Savings calculation automatically.
- **🏦 Savings Analysis** — income vs expense vs savings by month, savings % trend.
- **🔁 Recurring Tracker** — recurring spend by category/sub-category, average per
  month, trend over time — useful for spotting subscriptions worth cutting.
- **📈 Trends & Charts** — all-time monthly trend, category distribution, top 10,
  Fixed/Variable, Need/Want, budget vs actual across months.
- **📝 Monthly Review** — auto-generated answers to the 9 end-of-month questions
  (spend, top category, what rose/fell, unnecessary spend, recurring items to cut,
  savings, budget status, next-month suggestion).
- **📘 Yearly Review** — auto-generated annual review (income, expenses, savings,
  investments, fixed/variable, top 10 categories, highest months, biggest
  "Want" expenses, recurring expenses, budget performance, recommendations).
- **🗂️ Data / Export** — full table view + CSV download of every transaction.

## Notes

- All the "automatic calculations" (daily/monthly/yearly totals, %s, comparisons,
  fixed/variable, need/want, recurring, budget vs actual, savings, projections) are
  computed live with pandas from whichever database is connected — nothing is
  hand-entered.
- To back up your data: if using Postgres, use Neon's/Supabase's built-in backup or
  `pg_dump`; either way, the CSV export on the Data/Export page always works as a
  quick backup.
- This same `DATABASE_URL` approach also works unchanged on Streamlit Community
  Cloud, Railway, Fly.io, or any other host — just set the same environment
  variable there.
