"""
Personal Expense Tracker — Streamlit App
==========================================
A single-file Streamlit app backed by a database via SQLAlchemy.
Enter expenses daily; monthly & yearly summaries, budgets, savings,
fixed/variable, need/want, recurring tracking, and trend charts are
all computed automatically from the same running dataset (no need to
create a new file every month/year).

STORAGE:
- Locally: defaults to a SQLite file (expenses.db) next to this script.
- On Render (or any host with an ephemeral filesystem): set the DATABASE_URL
  environment variable to a free external Postgres connection string
  (e.g. from Neon.tech or Supabase) so your data survives restarts and
  redeploys. See README.md for step-by-step setup.

Run with:  streamlit run app.py
"""

import os
import calendar
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Float,
    Text as SAText, and_,
)
import streamlit as st

# ----------------------------------------------------------------------------
# CONFIG / CONSTANTS
# ----------------------------------------------------------------------------

# DATABASE_URL comes from the environment on Render (point it at a free
# Postgres instance from Neon.tech or Supabase — see README.md). If it's
# not set, we fall back to a local SQLite file for local development.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///expenses.db")
# Some providers (Render, Heroku) hand out "postgres://" URLs, but
# SQLAlchemy's modern driver name is "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
USING_EXTERNAL_DB = DATABASE_URL.startswith("postgresql://")

CURRENCY = "₹"

CATEGORY_SUBCATEGORIES = {
    "Groceries": ["Supermarket", "Kirana / Local Store", "Online Grocery", "Other"],
    "Food / Restaurants": ["Dining Out", "Food Delivery", "Cafe / Coffee", "Snacks", "Other"],
    "Vegetables / Fruits": ["Vegetables", "Fruits", "Other"],
    "Utilities": ["Gas Cylinder", "Maintenance / Society", "Other"],
    "Electricity": ["Bill Payment", "Other"],
    "Water": ["Bill Payment", "Other"],
    "Internet / Mobile": ["Broadband", "Mobile Recharge", "DTH / Cable", "Other"],
    "Rent / Home Loan": ["Rent", "Home Loan EMI", "Society Maintenance", "Other"],
    "Transportation / Fuel": ["Fuel", "Cab / Taxi", "Public Transport", "Parking", "Toll", "Other"],
    "Vehicle Maintenance": ["Service", "Repair", "Spare Parts", "Car/Bike Wash", "Other"],
    "Medical": ["Doctor Consultation", "Medicines", "Diagnostics / Lab", "Hospital", "Health Insurance Premium", "Other"],
    "Education": ["Tuition Fee", "School / College Fee", "Books / Study Material", "Online Courses", "Other"],
    "Insurance": ["Life Insurance", "Health Insurance", "Vehicle Insurance", "Home Insurance", "Other"],
    "Investments": ["Mutual Fund SIP", "Stocks", "FD / RD", "PPF / EPF", "Gold", "Other"],
    "Shopping": ["Clothing", "Electronics", "Accessories", "Online Shopping", "Other"],
    "Entertainment": ["Movies", "OTT Subscriptions", "Outings", "Events / Concerts", "Other"],
    "Travel": ["Flights", "Trains / Buses", "Hotels", "Local Transport", "Sightseeing", "Other"],
    "Personal Care": ["Salon / Grooming", "Gym / Fitness", "Cosmetics", "Spa / Wellness", "Other"],
    "Household": ["Furniture", "Appliances", "Repairs", "Cleaning Supplies", "Other"],
    "EMI / Loans": ["Personal Loan", "Car Loan", "Credit Card EMI", "Other"],
    "Bank / Financial Charges": ["Bank Fees", "Late Fees", "Interest Charges", "Card Annual Fee", "Other"],
    "Gifts / Donations": ["Gifts", "Charity", "Religious Donation", "Other"],
    "Children's Expenses": ["School Fee", "Tuition", "Toys", "Clothing", "Activities", "Other"],
    "Family Expenses": ["Parents", "Siblings", "Relatives", "Other"],
    "Miscellaneous": ["Other"],
}
CATEGORIES = list(CATEGORY_SUBCATEGORIES.keys())

# Categories treated as "Fixed" spend by default (recurring, largely non-discretionary)
FIXED_CATEGORIES = {
    "Rent / Home Loan", "EMI / Loans", "Insurance", "Internet / Mobile",
    "Utilities", "Electricity", "Water",
}

PAYMENT_METHODS = ["Cash", "Credit Card", "Debit Card", "UPI", "Net Banking", "Wallet", "Other"]
PERSON_OPTIONS = ["Self", "Spouse", "Child", "Parent", "Family", "Other"]

MONTH_NAMES = list(calendar.month_name)[1:]

# ----------------------------------------------------------------------------
# DATABASE LAYER (SQLAlchemy — works with local SQLite or a hosted Postgres)
# ----------------------------------------------------------------------------

@st.cache_resource
def get_engine():
    # pool_pre_ping avoids "server closed the connection" errors after a free
    # Postgres instance has been idle; pool_recycle keeps connections fresh.
    return create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=280)


engine = get_engine()
metadata = MetaData()

transactions_table = Table(
    "transactions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner", String(100)),
    Column("date", String(20), nullable=False),
    Column("day", String(20), nullable=False),
    Column("category", String(100), nullable=False),
    Column("subcategory", String(100)),
    Column("description", SAText),
    Column("payment_method", String(50)),
    Column("amount", Float, nullable=False),
    Column("need_want", String(10)),
    Column("recurring", String(20)),
    Column("fixed_variable", String(10)),
    Column("person", String(50)),
    Column("notes", SAText),
    Column("created_at", String(40)),
)

budgets_table = Table(
    "budgets", metadata,
    Column("owner", String(100), primary_key=True),
    Column("month", String(7), primary_key=True),
    Column("category", String(100), primary_key=True),
    Column("amount", Float, nullable=False),
)

income_table = Table(
    "income", metadata,
    Column("owner", String(100), primary_key=True),
    Column("month", String(7), primary_key=True),
    Column("amount", Float, nullable=False),
)

# Name used to tag any data that existed before per-user accounts were added,
# so nothing already saved gets lost — it just becomes visible under this
# username. Log in as this name once to see/reclaim older records.
LEGACY_OWNER = "legacy"


def init_db():
    # create_all emits the correct dialect-specific DDL (e.g. SERIAL vs
    # AUTOINCREMENT) automatically based on the engine, so this works
    # unchanged for both SQLite and Postgres. For tables that already existed
    # before the "owner" column was introduced, this alone won't add it —
    # _migrate_add_owner_columns() below handles that.
    metadata.create_all(engine)
    _migrate_add_owner_columns()


def _migrate_add_owner_columns():
    """Add an 'owner' column to any pre-existing tables that don't have one
    yet, and tag their existing rows as LEGACY_OWNER so old data is still
    reachable (by signing in with that username) instead of disappearing."""
    from sqlalchemy import inspect, text as satext
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    with engine.begin() as conn:
        for table_name in ["transactions", "budgets", "income"]:
            if table_name not in existing_tables:
                continue
            cols = [c["name"] for c in inspector.get_columns(table_name)]
            if "owner" not in cols:
                conn.execute(satext(f"ALTER TABLE {table_name} ADD COLUMN owner VARCHAR(100)"))
            conn.execute(satext(
                f"UPDATE {table_name} SET owner = :legacy WHERE owner IS NULL"
            ), {"legacy": LEGACY_OWNER})


def add_transaction(row: dict, owner: str):
    with engine.begin() as conn:
        conn.execute(transactions_table.insert().values(
            owner=owner, date=row["date"], day=row["day"], category=row["category"],
            subcategory=row["subcategory"], description=row["description"],
            payment_method=row["payment_method"], amount=row["amount"],
            need_want=row["need_want"], recurring=row["recurring"],
            fixed_variable=row["fixed_variable"], person=row["person"],
            notes=row["notes"], created_at=datetime.now().isoformat(),
        ))


def delete_transaction(tx_id: int, owner: str):
    # Filtering by owner too means a user can't delete another user's
    # transaction even if they guess/enter its numeric ID.
    with engine.begin() as conn:
        conn.execute(transactions_table.delete().where(
            and_(transactions_table.c.id == tx_id, transactions_table.c.owner == owner)
        ))


@st.cache_data(ttl=2)
def load_transactions(owner: str, _refresh_token=0) -> pd.DataFrame:
    from sqlalchemy import text as satext
    df = pd.read_sql_query(
        satext("SELECT * FROM transactions WHERE owner = :owner ORDER BY date ASC, id ASC"),
        engine, params={"owner": owner},
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["month_label"] = df["date"].dt.strftime("%Y-%m")
    df["month_name"] = df["date"].dt.strftime("%b %Y")
    return df


def set_budget(month: str, category: str, amount: float, owner: str):
    # Portable "upsert": delete any existing row then insert, inside one
    # transaction — avoids relying on dialect-specific ON CONFLICT syntax.
    with engine.begin() as conn:
        conn.execute(budgets_table.delete().where(
            and_(budgets_table.c.month == month, budgets_table.c.category == category,
                 budgets_table.c.owner == owner)
        ))
        conn.execute(budgets_table.insert().values(owner=owner, month=month, category=category, amount=amount))


@st.cache_data(ttl=2)
def get_budgets(owner: str, _refresh_token=0) -> pd.DataFrame:
    from sqlalchemy import text as satext
    return pd.read_sql_query(
        satext("SELECT * FROM budgets WHERE owner = :owner"), engine, params={"owner": owner}
    )


def set_income(month: str, amount: float, owner: str):
    with engine.begin() as conn:
        conn.execute(income_table.delete().where(
            and_(income_table.c.month == month, income_table.c.owner == owner)
        ))
        conn.execute(income_table.insert().values(owner=owner, month=month, amount=amount))


@st.cache_data(ttl=2)
def get_income(owner: str, _refresh_token=0) -> pd.DataFrame:
    from sqlalchemy import text as satext
    return pd.read_sql_query(
        satext("SELECT * FROM income WHERE owner = :owner"), engine, params={"owner": owner}
    )


def claim_legacy_data(owner: str):
    """One-time helper: re-tag any pre-account data (owner = LEGACY_OWNER)
    as belonging to the current user. Used from the sidebar so whoever used
    the app before accounts existed can pull their old records back in."""
    with engine.begin() as conn:
        for table in (transactions_table, budgets_table, income_table):
            conn.execute(table.update().where(table.c.owner == LEGACY_OWNER).values(owner=owner))


def bump_token():
    st.session_state["refresh_token"] = st.session_state.get("refresh_token", 0) + 1
    load_transactions.clear()
    get_budgets.clear()
    get_income.clear()


# ----------------------------------------------------------------------------
# FORMATTING HELPERS
# ----------------------------------------------------------------------------

def fmt(x):
    try:
        return f"{CURRENCY}{x:,.2f}"
    except Exception:
        return f"{CURRENCY}0.00"


def pct(x):
    try:
        return f"{x:,.1f}%"
    except Exception:
        return "0.0%"


def month_label_to_name(m):
    try:
        y, mo = m.split("-")
        return f"{calendar.month_abbr[int(mo)]} {y}"
    except Exception:
        return m


# ----------------------------------------------------------------------------
# APP SETUP
# ----------------------------------------------------------------------------

st.set_page_config(page_title="Personal Expense Tracker", page_icon="💰", layout="wide")
init_db()

# --- Simple name-based sign-in -----------------------------------------
# Not a secure login (no password) — it just tags every record with a
# username so multiple people can share one deployed app without seeing
# each other's data. Anyone who knows a username can view/enter data under
# it, so agree on unique names with whoever you share the link with.
if "username" not in st.session_state:
    st.title("💰 Personal Expense Tracker")
    st.subheader("👋 Who's tracking expenses?")
    with st.form("login_form"):
        uname = st.text_input("Enter your name", placeholder="e.g., Prakash")
        go = st.form_submit_button("Continue", type="primary", use_container_width=True)
        if go:
            clean = uname.strip()
            if not clean:
                st.error("Please enter a name.")
            else:
                st.session_state["username"] = clean.lower().replace(" ", "_")
                st.session_state["display_name"] = clean
                st.rerun()
    st.caption("This is simple name-based separation, not a password login — anyone who knows "
               "a username can see data saved under it. Use a distinct name per person sharing this app.")
    st.stop()

CURRENT_USER = st.session_state["username"]

token = st.session_state.get("refresh_token", 0)
df_all = load_transactions(CURRENT_USER, token)
budgets_all = get_budgets(CURRENT_USER, token)
income_all = get_income(CURRENT_USER, token)

st.sidebar.title("💰 Expense Tracker")
st.sidebar.markdown(f"👤 Signed in as **{st.session_state.get('display_name', CURRENT_USER)}**")
c_switch, c_claim = st.sidebar.columns(2)
if c_switch.button("🔓 Switch user", use_container_width=True):
    for k in ("username", "display_name"):
        st.session_state.pop(k, None)
    st.rerun()
if c_claim.button("📥 Claim old data", use_container_width=True,
                   help="Pulls in any records saved before per-user accounts existed."):
    claim_legacy_data(CURRENT_USER)
    bump_token()
    st.sidebar.success("Old data claimed.")
    st.rerun()

if USING_EXTERNAL_DB:
    st.sidebar.caption("🟢 Connected to external Postgres database")
else:
    st.sidebar.caption("🟡 Using local SQLite (won't persist on Render free tier)")
nav = st.sidebar.radio(
    "Navigate",
    [
        "➕ Add Expense",
        "📅 Daily Dashboard",
        "🗓️ Monthly Dashboard",
        "📆 Yearly Dashboard",
        "📊 Category Analysis",
        "💵 Budget & Income Settings",
        "🏦 Savings Analysis",
        "🔁 Recurring Tracker",
        "📈 Trends & Charts",
        "📝 Monthly Review",
        "📘 Yearly Review",
        "🗂️ Data / Export",
    ],
)

if not df_all.empty:
    st.sidebar.markdown("---")
    st.sidebar.caption(f"Total transactions logged: **{len(df_all)}**")
    st.sidebar.caption(f"Date range: {df_all['date'].min().date()} → {df_all['date'].max().date()}")

# ============================================================================
# PAGE: ADD EXPENSE
# ============================================================================
if nav == "➕ Add Expense":
    st.title("➕ Add a New Expense")
    st.caption("Enter one transaction at a time. Day, Fixed/Variable are derived automatically.")

    with st.form("add_expense_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            tx_date = st.date_input("Date", value=date.today())
            category = st.selectbox("Expense Category", CATEGORIES)
        with c2:
            subcategory = st.selectbox("Expense Sub-Category", CATEGORY_SUBCATEGORIES[category])
            payment_method = st.selectbox("Payment Method", PAYMENT_METHODS)
        with c3:
            amount = st.number_input("Amount", min_value=0.0, step=10.0, format="%.2f")
            person = st.selectbox("Person / Family Member", PERSON_OPTIONS)

        description = st.text_input("Description / Purpose", placeholder="e.g., Weekly grocery run at BigBasket")

        c4, c5 = st.columns(2)
        with c4:
            need_want = st.radio("Need / Want", ["Need", "Want"], horizontal=True)
        with c5:
            recurring = st.radio("Recurring / One-Time", ["Recurring", "One-Time"], horizontal=True)

        notes = st.text_area("Notes (optional)", height=60)

        submitted = st.form_submit_button("💾 Save Expense", use_container_width=True, type="primary")

        if submitted:
            if amount <= 0:
                st.error("Please enter an amount greater than 0.")
            else:
                day_name = tx_date.strftime("%A")
                fixed_variable = "Fixed" if category in FIXED_CATEGORIES else "Variable"
                add_transaction({
                    "date": tx_date.isoformat(),
                    "day": day_name,
                    "category": category,
                    "subcategory": subcategory,
                    "description": description,
                    "payment_method": payment_method,
                    "amount": amount,
                    "need_want": need_want,
                    "recurring": recurring,
                    "fixed_variable": fixed_variable,
                    "person": person,
                    "notes": notes,
                }, CURRENT_USER)
                bump_token()
                st.success(f"Saved: {fmt(amount)} — {category} / {subcategory} on {tx_date}")
                st.rerun()

    st.markdown("---")
    st.subheader("Recent Entries")
    if df_all.empty:
        st.info("No expenses logged yet. Add your first one above!")
    else:
        recent = df_all.sort_values("date", ascending=False).head(15)
        show_cols = ["date", "day", "category", "subcategory", "amount", "payment_method",
                     "need_want", "recurring", "person"]
        st.dataframe(recent[show_cols].rename(columns={
            "date": "Date", "day": "Day", "category": "Category", "subcategory": "Sub-Category",
            "amount": "Amount", "payment_method": "Payment", "need_want": "Need/Want",
            "recurring": "Recurring", "person": "Person"
        }), use_container_width=True, hide_index=True)

        with st.expander("🗑️ Delete a transaction"):
            del_id = st.number_input("Transaction ID to delete", min_value=1, step=1)
            if st.button("Delete"):
                delete_transaction(int(del_id), CURRENT_USER)
                bump_token()
                st.success(f"Deleted transaction {del_id}")
                st.rerun()

# ============================================================================
# PAGE: DAILY DASHBOARD
# ============================================================================
elif nav == "📅 Daily Dashboard":
    st.title("📅 Daily Expense Summary")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        sel_date = st.date_input("Select date", value=df_all["date"].max().date())
        day_df = df_all[df_all["date"].dt.date == sel_date]

        if day_df.empty:
            st.warning("No transactions on this date.")
        else:
            total = day_df["amount"].sum()
            count = len(day_df)
            highest = day_df.loc[day_df["amount"].idxmax()]

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Expense for the Day", fmt(total))
            c2.metric("Number of Transactions", count)
            c3.metric("Highest Expense", fmt(highest["amount"]), help=f"{highest['category']} - {highest['description']}")

            st.markdown("#### Category-wise Spending")
            cat_sum = day_df.groupby("category")["amount"].sum().sort_values(ascending=False)
            fig = px.bar(cat_sum, x=cat_sum.values, y=cat_sum.index, orientation="h",
                         labels={"x": "Amount", "y": "Category"}, text_auto=".2s")
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

            c4, c5 = st.columns(2)
            with c4:
                st.markdown("#### Need vs Want")
                nw = day_df.groupby("need_want")["amount"].sum()
                st.plotly_chart(px.pie(nw, values=nw.values, names=nw.index, hole=0.45),
                                 use_container_width=True)
            with c5:
                st.markdown("#### Recurring vs One-Time")
                rc = day_df.groupby("recurring")["amount"].sum()
                st.plotly_chart(px.pie(rc, values=rc.values, names=rc.index, hole=0.45),
                                 use_container_width=True)

            st.markdown("#### All Transactions on This Day")
            st.dataframe(day_df[["date", "category", "subcategory", "description", "amount",
                                  "payment_method", "need_want", "recurring", "person", "notes"]],
                         use_container_width=True, hide_index=True)

# ============================================================================
# PAGE: MONTHLY DASHBOARD
# ============================================================================
elif nav == "🗓️ Monthly Dashboard":
    st.title("🗓️ Monthly Expense Summary")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        years = sorted(df_all["year"].unique(), reverse=True)
        c1, c2 = st.columns(2)
        sel_year = c1.selectbox("Year", years)
        months_available = sorted(df_all[df_all["year"] == sel_year]["month"].unique())
        sel_month = c2.selectbox("Month", months_available, format_func=lambda m: calendar.month_name[m],
                                  index=len(months_available) - 1)

        month_str = f"{sel_year}-{sel_month:02d}"
        mdf = df_all[(df_all["year"] == sel_year) & (df_all["month"] == sel_month)]

        prev_month_date = date(sel_year, sel_month, 1) - timedelta(days=1)
        prev_month_str = prev_month_date.strftime("%Y-%m")
        prev_mdf = df_all[df_all["month_label"] == prev_month_str]

        total_month = mdf["amount"].sum()
        num_days_with_data = mdf["date"].dt.date.nunique()
        days_in_month = calendar.monthrange(sel_year, sel_month)[1]
        avg_daily = total_month / days_in_month if days_in_month else 0
        num_tx = len(mdf)

        income_row = income_all[income_all["month"] == month_str]
        income_amt = float(income_row["amount"].iloc[0]) if not income_row.empty else 0.0
        savings = income_amt - total_month
        savings_pct = (savings / income_amt * 100) if income_amt > 0 else 0

        budget_rows = budgets_all[budgets_all["month"] == month_str]
        total_budget = budget_rows["amount"].sum() if not budget_rows.empty else 0.0
        remaining_budget = total_budget - total_month

        prev_total = prev_mdf["amount"].sum()
        mom_change = total_month - prev_total
        mom_pct = (mom_change / prev_total * 100) if prev_total > 0 else 0

        st.subheader(f"{calendar.month_name[sel_month]} {sel_year}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Monthly Expense", fmt(total_month))
        c2.metric("Average Daily Expense", fmt(avg_daily))
        c3.metric("Number of Transactions", num_tx)
        c4.metric("Month-over-Month", fmt(mom_change), delta=f"{mom_pct:+.1f}%", delta_color="inverse")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Monthly Budget", fmt(total_budget) if total_budget else "Not Set")
        c6.metric("Remaining Budget", fmt(remaining_budget) if total_budget else "—",
                  delta=None if not total_budget else ("Over budget" if remaining_budget < 0 else "On track"))
        c7.metric("Savings", fmt(savings) if income_amt else "Set income")
        c8.metric("Savings %", pct(savings_pct) if income_amt else "—")

        if not mdf.empty:
            cat_sum = mdf.groupby("category")["amount"].sum().sort_values(ascending=False)
            highest_cat = cat_sum.index[0]
            day_sum = mdf.groupby(mdf["date"].dt.date)["amount"].sum().sort_values(ascending=False)
            highest_day = day_sum.index[0]
            st.info(f"🏆 **Highest Spending Category:** {highest_cat} ({fmt(cat_sum.iloc[0])})  |  "
                    f"📆 **Highest Spending Day:** {highest_day} ({fmt(day_sum.iloc[0])})")

        st.markdown("---")
        colA, colB = st.columns(2)
        with colA:
            st.markdown("#### Category-wise Monthly Expense (% of total)")
            cat_pct = (mdf.groupby("category")["amount"].sum().sort_values(ascending=False) / total_month * 100) if total_month else pd.Series(dtype=float)
            if not cat_pct.empty:
                fig = px.bar(cat_pct, x=cat_pct.values, y=cat_pct.index, orientation="h",
                             labels={"x": "% of Monthly Total", "y": "Category"}, text_auto=".1f")
                fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig, use_container_width=True)
        with colB:
            st.markdown("#### Category Distribution")
            if not mdf.empty:
                cs = mdf.groupby("category")["amount"].sum()
                st.plotly_chart(px.pie(cs, values=cs.values, names=cs.index, hole=0.4), use_container_width=True)

        colC, colD, colE = st.columns(3)
        with colC:
            st.markdown("#### Fixed vs Variable")
            fv = mdf.groupby("fixed_variable")["amount"].sum()
            st.plotly_chart(px.pie(fv, values=fv.values, names=fv.index, hole=0.45), use_container_width=True)
        with colD:
            st.markdown("#### Need vs Want")
            nw = mdf.groupby("need_want")["amount"].sum()
            st.plotly_chart(px.pie(nw, values=nw.values, names=nw.index, hole=0.45), use_container_width=True)
        with colE:
            st.markdown("#### Recurring vs One-Time")
            rc = mdf.groupby("recurring")["amount"].sum()
            st.plotly_chart(px.pie(rc, values=rc.values, names=rc.index, hole=0.45), use_container_width=True)

        st.markdown("#### Daily Spending Through the Month")
        if not mdf.empty:
            daily = mdf.groupby(mdf["date"].dt.date)["amount"].sum().reset_index()
            fig = px.bar(daily, x="date", y="amount", labels={"date": "Date", "amount": "Amount"})
            fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Budget vs Actual by Category")
        if not budget_rows.empty:
            actual_cat = mdf.groupby("category")["amount"].sum()
            comp = budget_rows.set_index("category")["amount"].to_frame("Budget")
            comp["Actual"] = actual_cat.reindex(comp.index).fillna(0)
            comp = comp.reset_index().rename(columns={"category": "Category"})
            fig = go.Figure()
            fig.add_bar(name="Budget", x=comp["Category"], y=comp["Budget"])
            fig.add_bar(name="Actual", x=comp["Category"], y=comp["Actual"])
            fig.update_layout(barmode="group", height=380, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No category budgets set for this month yet — set them in 'Budget & Income Settings'.")

# ============================================================================
# PAGE: YEARLY DASHBOARD
# ============================================================================
elif nav == "📆 Yearly Dashboard":
    st.title("📆 Yearly Expense Summary")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        years = sorted(df_all["year"].unique(), reverse=True)
        sel_year = st.selectbox("Year", years)
        ydf = df_all[df_all["year"] == sel_year]

        total_year = ydf["amount"].sum()
        months_with_data = ydf["month"].nunique()
        avg_monthly = total_year / months_with_data if months_with_data else 0
        days_elapsed = ydf["date"].dt.date.nunique()
        first_day = date(sel_year, 1, 1)
        last_day = ydf["date"].max().date() if not ydf.empty else first_day
        span_days = (last_day - first_day).days + 1
        avg_daily = total_year / span_days if span_days else 0

        monthly_totals = ydf.groupby("month")["amount"].sum()
        highest_month = monthly_totals.idxmax() if not monthly_totals.empty else None
        lowest_month = monthly_totals.idxmin() if not monthly_totals.empty else None
        cat_totals = ydf.groupby("category")["amount"].sum().sort_values(ascending=False)
        highest_cat = cat_totals.index[0] if not cat_totals.empty else "—"

        income_year = income_all[income_all["month"].str.startswith(str(sel_year))]
        total_income = income_year["amount"].sum() if not income_year.empty else 0.0
        total_savings = total_income - total_year
        savings_pct = (total_savings / total_income * 100) if total_income > 0 else 0

        budget_year = budgets_all[budgets_all["month"].str.startswith(str(sel_year))]
        annual_budget = budget_year["amount"].sum() if not budget_year.empty else 0.0

        today = date.today()
        if sel_year == today.year:
            ytd = ydf[ydf["date"].dt.date <= today]["amount"].sum()
            days_so_far = (today - first_day).days + 1
            projected = (ytd / days_so_far) * 365 if days_so_far else 0
        else:
            ytd = total_year
            projected = total_year

        st.subheader(f"Year {sel_year}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Annual Expense", fmt(total_year))
        c2.metric("Average Monthly Expense", fmt(avg_monthly))
        c3.metric("Average Daily Expense", fmt(avg_daily))
        c4.metric("Year-to-Date Expense", fmt(ytd))

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Annual Budget", fmt(annual_budget) if annual_budget else "Not Set")
        c6.metric("Budget Remaining", fmt(annual_budget - total_year) if annual_budget else "—")
        c7.metric("Total Savings", fmt(total_savings) if total_income else "Set income")
        c8.metric("Savings %", pct(savings_pct) if total_income else "—")

        st.info(
            f"🏆 **Highest Spending Month:** {calendar.month_name[highest_month] if highest_month else '—'} "
            f"({fmt(monthly_totals.max()) if not monthly_totals.empty else '—'})  |  "
            f"📉 **Lowest Spending Month:** {calendar.month_name[lowest_month] if lowest_month else '—'} "
            f"({fmt(monthly_totals.min()) if not monthly_totals.empty else '—'})  |  "
            f"🥇 **Top Category:** {highest_cat}  |  "
            f"🔮 **Projected Annual Expense:** {fmt(projected)}"
        )

        st.markdown("---")
        colA, colB = st.columns(2)
        with colA:
            st.markdown("#### Monthly Expense Trend")
            mt = monthly_totals.reindex(range(1, 13), fill_value=0)
            fig = px.line(x=[calendar.month_abbr[m] for m in mt.index], y=mt.values, markers=True,
                          labels={"x": "Month", "y": "Amount"})
            fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
        with colB:
            st.markdown("#### Category-wise Annual Expense")
            fig = px.pie(cat_totals, values=cat_totals.values, names=cat_totals.index, hole=0.4)
            fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

        colC, colD, colE = st.columns(3)
        with colC:
            st.markdown("#### Fixed vs Variable")
            fv = ydf.groupby("fixed_variable")["amount"].sum()
            st.plotly_chart(px.pie(fv, values=fv.values, names=fv.index, hole=0.45), use_container_width=True)
        with colD:
            st.markdown("#### Need vs Want")
            nw = ydf.groupby("need_want")["amount"].sum()
            st.plotly_chart(px.pie(nw, values=nw.values, names=nw.index, hole=0.45), use_container_width=True)
        with colE:
            st.markdown("#### Recurring vs One-Time")
            rc = ydf.groupby("recurring")["amount"].sum()
            st.plotly_chart(px.pie(rc, values=rc.values, names=rc.index, hole=0.45), use_container_width=True)

        st.markdown("#### Month-over-Month Growth / Reduction")
        mt_df = mt.reset_index()
        mt_df.columns = ["month", "amount"]
        mt_df["change_pct"] = mt_df["amount"].pct_change().fillna(0) * 100
        mt_df["month_name"] = mt_df["month"].apply(lambda m: calendar.month_abbr[m])
        fig = px.bar(mt_df, x="month_name", y="change_pct", labels={"month_name": "Month", "change_pct": "% Change vs Prior Month"})
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Annual Budget vs Actual (Monthly)")
        if not budget_year.empty:
            budget_monthly = budget_year.copy()
            budget_monthly["month_num"] = budget_monthly["month"].str.split("-").str[1].astype(int)
            budget_monthly = budget_monthly.groupby("month_num")["amount"].sum().reindex(range(1, 13), fill_value=0)
            fig = go.Figure()
            fig.add_bar(name="Budget", x=[calendar.month_abbr[m] for m in range(1, 13)], y=budget_monthly.values)
            fig.add_bar(name="Actual", x=[calendar.month_abbr[m] for m in range(1, 13)], y=mt.values)
            fig.update_layout(barmode="group", height=380, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No budgets set for this year yet.")

# ============================================================================
# PAGE: CATEGORY ANALYSIS
# ============================================================================
elif nav == "📊 Category Analysis":
    st.title("📊 Category-wise Analysis")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        c1, c2 = st.columns(2)
        date_range = c1.date_input("Date range", value=(df_all["date"].min().date(), df_all["date"].max().date()))
        cat_filter = c2.multiselect("Filter categories (optional)", CATEGORIES)

        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_d, end_d = date_range
        else:
            start_d, end_d = df_all["date"].min().date(), df_all["date"].max().date()

        fdf = df_all[(df_all["date"].dt.date >= start_d) & (df_all["date"].dt.date <= end_d)]
        if cat_filter:
            fdf = fdf[fdf["category"].isin(cat_filter)]

        if fdf.empty:
            st.warning("No transactions in this range.")
        else:
            total = fdf["amount"].sum()
            st.metric("Total Spend in Range", fmt(total))

            st.markdown("#### Top 10 Expense Categories")
            top10 = fdf.groupby("category")["amount"].sum().sort_values(ascending=False).head(10)
            fig = px.bar(top10, x=top10.values, y=top10.index, orientation="h", text_auto=".2s",
                         labels={"x": "Amount", "y": "Category"})
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("#### Category → Sub-Category Breakdown")
            sub = fdf.groupby(["category", "subcategory"])["amount"].sum().reset_index()
            fig = px.treemap(sub, path=["category", "subcategory"], values="amount")
            fig.update_layout(height=450, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("#### Category Summary Table")
            summary = fdf.groupby("category").agg(
                Total=("amount", "sum"),
                Transactions=("amount", "count"),
                Average=("amount", "mean"),
            ).sort_values("Total", ascending=False)
            summary["% of Total"] = (summary["Total"] / total * 100).round(1)
            summary["Total"] = summary["Total"].map(fmt)
            summary["Average"] = summary["Average"].map(fmt)
            st.dataframe(summary, use_container_width=True)

# ============================================================================
# PAGE: BUDGET & INCOME SETTINGS
# ============================================================================
elif nav == "💵 Budget & Income Settings":
    st.title("💵 Budget & Income Settings")
    st.caption("Set your monthly income and category-wise budgets. These drive Budget vs Actual and Savings calculations across the app.")

    sel_month = st.date_input("Select month to configure", value=date.today().replace(day=1))
    month_str = sel_month.strftime("%Y-%m")

    st.subheader(f"Income — {month_label_to_name(month_str)}")
    existing_income = income_all[income_all["month"] == month_str]
    default_income = float(existing_income["amount"].iloc[0]) if not existing_income.empty else 0.0
    inc_val = st.number_input("Monthly Income", min_value=0.0, value=default_income, step=1000.0, format="%.2f")
    if st.button("💾 Save Income"):
        set_income(month_str, inc_val, CURRENT_USER)
        bump_token()
        st.success(f"Income for {month_label_to_name(month_str)} saved: {fmt(inc_val)}")
        st.rerun()

    st.markdown("---")
    st.subheader(f"Category Budgets — {month_label_to_name(month_str)}")
    existing_budgets = budgets_all[budgets_all["month"] == month_str].set_index("category")["amount"].to_dict()

    with st.form("budget_form"):
        budget_inputs = {}
        cols = st.columns(3)
        for i, cat in enumerate(CATEGORIES):
            with cols[i % 3]:
                budget_inputs[cat] = st.number_input(
                    cat, min_value=0.0, value=float(existing_budgets.get(cat, 0.0)),
                    step=500.0, key=f"budget_{cat}"
                )
        save_budgets = st.form_submit_button("💾 Save All Category Budgets", type="primary")
        if save_budgets:
            for cat, amt in budget_inputs.items():
                if amt > 0:
                    set_budget(month_str, cat, amt, CURRENT_USER)
            bump_token()
            st.success(f"Budgets saved for {month_label_to_name(month_str)}. Total budget: {fmt(sum(budget_inputs.values()))}")
            st.rerun()

    st.markdown("---")
    st.subheader("All Saved Income Records")
    if not income_all.empty:
        st.dataframe(income_all.assign(month_name=income_all["month"].apply(month_label_to_name))
                     [["month_name", "amount"]].rename(columns={"month_name": "Month", "amount": "Income"}),
                     use_container_width=True, hide_index=True)
    st.subheader("All Saved Budgets")
    if not budgets_all.empty:
        bt = budgets_all.copy()
        bt["Month"] = bt["month"].apply(month_label_to_name)
        st.dataframe(bt[["Month", "category", "amount"]].rename(columns={"category": "Category", "amount": "Budget"}),
                     use_container_width=True, hide_index=True)

# ============================================================================
# PAGE: SAVINGS ANALYSIS
# ============================================================================
elif nav == "🏦 Savings Analysis":
    st.title("🏦 Savings Analysis")

    if income_all.empty:
        st.info("Set your monthly income in 'Budget & Income Settings' to see savings analysis.")
    else:
        merged = income_all.copy()
        exp_by_month = df_all.groupby("month_label")["amount"].sum() if not df_all.empty else pd.Series(dtype=float)
        merged["expense"] = merged["month"].map(exp_by_month).fillna(0)
        merged["savings"] = merged["amount"] - merged["expense"]
        merged["savings_pct"] = np.where(merged["amount"] > 0, merged["savings"] / merged["amount"] * 100, 0)
        merged["month_name"] = merged["month"].apply(month_label_to_name)
        merged = merged.sort_values("month")

        total_income = merged["amount"].sum()
        total_expense = merged["expense"].sum()
        total_savings = merged["savings"].sum()
        overall_pct = (total_savings / total_income * 100) if total_income else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Income (tracked months)", fmt(total_income))
        c2.metric("Total Expense (tracked months)", fmt(total_expense))
        c3.metric("Total Savings", fmt(total_savings))
        c4.metric("Overall Savings %", pct(overall_pct))

        st.markdown("#### Monthly Savings Trend")
        fig = go.Figure()
        fig.add_bar(name="Income", x=merged["month_name"], y=merged["amount"])
        fig.add_bar(name="Expense", x=merged["month_name"], y=merged["expense"])
        fig.add_scatter(name="Savings", x=merged["month_name"], y=merged["savings"], mode="lines+markers", yaxis="y")
        fig.update_layout(barmode="group", height=420, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Savings % by Month")
        fig2 = px.line(merged, x="month_name", y="savings_pct", markers=True,
                       labels={"month_name": "Month", "savings_pct": "Savings %"})
        fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig2, use_container_width=True)

        st.markdown("#### Details")
        show = merged[["month_name", "amount", "expense", "savings", "savings_pct"]].rename(columns={
            "month_name": "Month", "amount": "Income", "expense": "Expense",
            "savings": "Savings", "savings_pct": "Savings %"
        })
        show["Income"] = show["Income"].map(fmt)
        show["Expense"] = show["Expense"].map(fmt)
        show["Savings"] = show["Savings"].map(fmt)
        show["Savings %"] = show["Savings %"].map(pct)
        st.dataframe(show, use_container_width=True, hide_index=True)

# ============================================================================
# PAGE: RECURRING TRACKER
# ============================================================================
elif nav == "🔁 Recurring Tracker":
    st.title("🔁 Recurring Expense Tracker")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        rec_df = df_all[df_all["recurring"] == "Recurring"]
        if rec_df.empty:
            st.info("No recurring expenses logged yet.")
        else:
            latest_month = df_all["month_label"].max()
            this_month_rec = rec_df[rec_df["month_label"] == latest_month]

            c1, c2 = st.columns(2)
            c1.metric(f"Recurring Spend — {month_label_to_name(latest_month)}", fmt(this_month_rec["amount"].sum()))
            c2.metric("Recurring as % of that Month's Total",
                     pct(this_month_rec["amount"].sum() / df_all[df_all["month_label"] == latest_month]["amount"].sum() * 100)
                     if df_all[df_all["month_label"] == latest_month]["amount"].sum() else "0.0%")

            st.markdown("#### Recurring Expenses by Category / Sub-Category (all-time avg per month)")
            months_count = df_all["month_label"].nunique()
            rec_summary = rec_df.groupby(["category", "subcategory"]).agg(
                Total=("amount", "sum"),
                Count=("amount", "count"),
            ).reset_index()
            rec_summary["Avg / Month"] = rec_summary["Total"] / max(months_count, 1)
            rec_summary = rec_summary.sort_values("Total", ascending=False)
            rec_summary["Total"] = rec_summary["Total"].map(fmt)
            rec_summary["Avg / Month"] = rec_summary["Avg / Month"].map(fmt)
            st.dataframe(rec_summary.rename(columns={"category": "Category", "subcategory": "Sub-Category"}),
                         use_container_width=True, hide_index=True)

            st.markdown("#### Recurring Spend Trend Over Time")
            trend = rec_df.groupby("month_label")["amount"].sum().reset_index()
            trend["month_name"] = trend["month_label"].apply(month_label_to_name)
            fig = px.line(trend, x="month_name", y="amount", markers=True,
                         labels={"month_name": "Month", "amount": "Recurring Spend"})
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("#### All Recurring Transactions")
            st.dataframe(rec_df[["date", "category", "subcategory", "description", "amount", "person"]]
                         .sort_values("date", ascending=False),
                         use_container_width=True, hide_index=True)

# ============================================================================
# PAGE: TRENDS & CHARTS
# ============================================================================
elif nav == "📈 Trends & Charts":
    st.title("📈 Expense Trends & Charts")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        st.markdown("#### Overall Monthly Expense Trend (all years)")
        trend = df_all.groupby("month_label")["amount"].sum().reset_index().sort_values("month_label")
        trend["month_name"] = trend["month_label"].apply(month_label_to_name)
        fig = px.line(trend, x="month_name", y="amount", markers=True,
                     labels={"month_name": "Month", "amount": "Total Expense"})
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Category-wise Expense Distribution (all-time)")
        cat_all = df_all.groupby("category")["amount"].sum().sort_values(ascending=False)
        fig = px.pie(cat_all, values=cat_all.values, names=cat_all.index, hole=0.4)
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Top 10 Expense Categories (all-time)")
        top10 = cat_all.head(10)
        fig = px.bar(top10, x=top10.values, y=top10.index, orientation="h", text_auto=".2s",
                     labels={"x": "Amount", "y": "Category"})
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        colA, colB = st.columns(2)
        with colA:
            st.markdown("#### Fixed vs Variable (all-time)")
            fv = df_all.groupby("fixed_variable")["amount"].sum()
            st.plotly_chart(px.pie(fv, values=fv.values, names=fv.index, hole=0.45), use_container_width=True)
        with colB:
            st.markdown("#### Need vs Want (all-time)")
            nw = df_all.groupby("need_want")["amount"].sum()
            st.plotly_chart(px.pie(nw, values=nw.values, names=nw.index, hole=0.45), use_container_width=True)

        if not budgets_all.empty:
            st.markdown("#### Monthly Budget vs Actual (all months with a budget set)")
            b = budgets_all.groupby("month")["amount"].sum().reset_index().rename(columns={"amount": "Budget"})
            a = df_all.groupby("month_label")["amount"].sum().reset_index().rename(columns={"month_label": "month", "amount": "Actual"})
            comp = b.merge(a, on="month", how="left").fillna(0).sort_values("month")
            comp["month_name"] = comp["month"].apply(month_label_to_name)
            fig = go.Figure()
            fig.add_bar(name="Budget", x=comp["month_name"], y=comp["Budget"])
            fig.add_bar(name="Actual", x=comp["month_name"], y=comp["Actual"])
            fig.update_layout(barmode="group", height=380, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

# ============================================================================
# PAGE: MONTHLY REVIEW
# ============================================================================
elif nav == "📝 Monthly Review":
    st.title("📝 End-of-Month Financial Summary")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        years = sorted(df_all["year"].unique(), reverse=True)
        c1, c2 = st.columns(2)
        sel_year = c1.selectbox("Year", years, key="review_year")
        months_available = sorted(df_all[df_all["year"] == sel_year]["month"].unique())
        sel_month = c2.selectbox("Month", months_available, format_func=lambda m: calendar.month_name[m],
                                  index=len(months_available) - 1, key="review_month")

        month_str = f"{sel_year}-{sel_month:02d}"
        mdf = df_all[df_all["month_label"] == month_str]
        prev_month_date = date(sel_year, sel_month, 1) - timedelta(days=1)
        prev_str = prev_month_date.strftime("%Y-%m")
        pdf_ = df_all[df_all["month_label"] == prev_str]

        total_month = mdf["amount"].sum()
        cat_now = mdf.groupby("category")["amount"].sum()
        cat_prev = pdf_.groupby("category")["amount"].sum()
        comp = pd.DataFrame({"Now": cat_now, "Prev": cat_prev}).fillna(0)
        comp["Change"] = comp["Now"] - comp["Prev"]
        increased = comp[comp["Change"] > 0].sort_values("Change", ascending=False)
        decreased = comp[comp["Change"] < 0].sort_values("Change")

        income_row = income_all[income_all["month"] == month_str]
        income_amt = float(income_row["amount"].iloc[0]) if not income_row.empty else 0.0
        savings = income_amt - total_month
        budget_rows = budgets_all[budgets_all["month"] == month_str]
        total_budget = budget_rows["amount"].sum() if not budget_rows.empty else 0.0
        within_budget = total_month <= total_budget if total_budget else None

        wants = mdf[mdf["need_want"] == "Want"].sort_values("amount", ascending=False)
        rec_df = mdf[mdf["recurring"] == "Recurring"]

        st.subheader(f"{calendar.month_name[sel_month]} {sel_year} — Summary")

        st.markdown(f"**1. How much did I spend?**  {fmt(total_month)} across {len(mdf)} transactions.")

        if not cat_now.empty:
            top_cat = cat_now.sort_values(ascending=False)
            st.markdown(f"**2. Where did I spend the most?**  {top_cat.index[0]} — {fmt(top_cat.iloc[0])} "
                        f"({top_cat.iloc[0]/total_month*100:.1f}% of the month).")
        else:
            st.markdown("**2. Where did I spend the most?**  No data.")

        if not increased.empty:
            inc_list = ", ".join([f"{c} ({fmt(v)} ↑)" for c, v in increased['Change'].head(5).items()])
            st.markdown(f"**3. Which expenses increased?**  {inc_list}")
        else:
            st.markdown("**3. Which expenses increased?**  None vs. last month.")

        if not decreased.empty:
            dec_list = ", ".join([f"{c} ({fmt(abs(v))} ↓)" for c, v in decreased['Change'].head(5).items()])
            st.markdown(f"**4. Which expenses decreased?**  {dec_list}")
        else:
            st.markdown("**4. Which expenses decreased?**  None vs. last month.")

        if not wants.empty:
            top_wants = wants.groupby("category")["amount"].sum().sort_values(ascending=False).head(5)
            want_list = ", ".join([f"{c} ({fmt(v)})" for c, v in top_wants.items()])
            st.markdown(f"**5. What were my unnecessary (Want) expenses?**  {want_list}  "
                        f"— total Wants: {fmt(wants['amount'].sum())}")
        else:
            st.markdown("**5. What were my unnecessary (Want) expenses?**  None logged.")

        if not rec_df.empty:
            rec_top = rec_df.groupby(["category", "subcategory"])["amount"].sum().sort_values(ascending=False).head(5)
            rec_list = ", ".join([f"{c[0]}/{c[1]} ({fmt(v)})" for c, v in rec_top.items()])
            st.markdown(f"**6. Recurring expenses that could be reduced?**  Review: {rec_list}")
        else:
            st.markdown("**6. Recurring expenses that could be reduced?**  None logged.")

        st.markdown(f"**7. How much did I save?**  "
                    f"{fmt(savings) if income_amt else 'Set your monthly income to calculate savings.'}")

        if within_budget is not None:
            st.markdown(f"**8. Did I stay within my budget?**  "
                        f"{'✅ Yes — ' + fmt(total_budget - total_month) + ' remaining' if within_budget else '⚠️ No — over by ' + fmt(total_month - total_budget)}")
        else:
            st.markdown("**8. Did I stay within my budget?**  No budget set for this month.")

        suggestions = []
        if not wants.empty and total_month > 0 and wants["amount"].sum() / total_month > 0.3:
            suggestions.append("Wants made up a large share of spend — look for discretionary cuts (dining out, shopping, entertainment).")
        if not increased.empty:
            suggestions.append(f"Watch {increased.index[0]} — it rose the most vs. last month.")
        if within_budget is False:
            suggestions.append("Reset next month's budget to be realistic, or trim the top overspend category.")
        if not suggestions:
            suggestions.append("Spending looks stable — keep tracking and consider increasing your savings/investment allocation.")
        st.markdown("**9. What should I improve next month?**  " + " ".join(suggestions))

# ============================================================================
# PAGE: YEARLY REVIEW
# ============================================================================
elif nav == "📘 Yearly Review":
    st.title("📘 Annual Financial Review")

    if df_all.empty:
        st.info("No data yet. Add expenses first.")
    else:
        years = sorted(df_all["year"].unique(), reverse=True)
        sel_year = st.selectbox("Year", years, key="yreview_year")
        ydf = df_all[df_all["year"] == sel_year]

        income_year = income_all[income_all["month"].str.startswith(str(sel_year))]
        total_income = income_year["amount"].sum() if not income_year.empty else 0.0
        total_expense = ydf["amount"].sum()
        total_savings = total_income - total_expense
        savings_pct = (total_savings / total_income * 100) if total_income else 0
        total_investments = ydf[ydf["category"] == "Investments"]["amount"].sum()

        fixed_total = ydf[ydf["fixed_variable"] == "Fixed"]["amount"].sum()
        variable_total = ydf[ydf["fixed_variable"] == "Variable"]["amount"].sum()

        top10 = ydf.groupby("category")["amount"].sum().sort_values(ascending=False).head(10)
        monthly_totals = ydf.groupby("month")["amount"].sum().reindex(range(1, 13), fill_value=0)
        highest_months = monthly_totals.sort_values(ascending=False).head(3)

        wants = ydf[ydf["need_want"] == "Want"]
        biggest_unnecessary = wants.sort_values("amount", ascending=False).head(5)

        rec_df = ydf[ydf["recurring"] == "Recurring"]
        rec_by_cat = rec_df.groupby("category")["amount"].sum().sort_values(ascending=False)

        budget_year = budgets_all[budgets_all["month"].str.startswith(str(sel_year))]
        annual_budget = budget_year["amount"].sum() if not budget_year.empty else 0.0

        st.subheader(f"Annual Review — {sel_year}")

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Income", fmt(total_income) if total_income else "Not set")
        c2.metric("Total Expenses", fmt(total_expense))
        c3.metric("Total Savings", fmt(total_savings) if total_income else "—")

        c4, c5, c6 = st.columns(3)
        c4.metric("Savings %", pct(savings_pct) if total_income else "—")
        c5.metric("Total Investments", fmt(total_investments))
        c6.metric("Fixed vs Variable", f"{fmt(fixed_total)} / {fmt(variable_total)}")

        st.markdown("---")
        st.markdown(f"**Top 10 Expense Categories:** " +
                    ", ".join([f"{c} ({fmt(v)})" for c, v in top10.items()]))

        st.markdown(f"**Highest Spending Months:** " +
                    ", ".join([f"{calendar.month_name[m]} ({fmt(v)})" for m, v in highest_months.items()]))

        if not biggest_unnecessary.empty:
            st.markdown("**Biggest Unnecessary (Want) Expenses:** " +
                        ", ".join([f"{r['category']} - {r['description'] or r['subcategory']} ({fmt(r['amount'])})"
                                   for _, r in biggest_unnecessary.iterrows()]))
        else:
            st.markdown("**Biggest Unnecessary (Want) Expenses:** None logged.")

        if not rec_by_cat.empty:
            st.markdown("**Recurring Expenses (by category):** " +
                        ", ".join([f"{c} ({fmt(v)})" for c, v in rec_by_cat.head(8).items()]))

        if annual_budget:
            perf = "under budget ✅" if total_expense <= annual_budget else "over budget ⚠️"
            st.markdown(f"**Budget Performance:** {fmt(total_expense)} spent vs {fmt(annual_budget)} budgeted — {perf}.")
        else:
            st.markdown("**Budget Performance:** No annual budget set.")

        st.markdown("#### Monthly Spending Trend")
        fig = px.line(x=[calendar.month_abbr[m] for m in monthly_totals.index], y=monthly_totals.values, markers=True,
                     labels={"x": "Month", "y": "Amount"})
        fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Recommendations")
        recs = []
        if not top10.empty:
            recs.append(f"Focus reduction efforts on **{top10.index[0]}**, your largest category ({fmt(top10.iloc[0])}).")
        if not wants.empty and total_expense > 0:
            want_share = wants["amount"].sum() / total_expense * 100
            recs.append(f"Discretionary (Want) spend was {want_share:.1f}% of total — consider trimming to below 25-30%.")
        if total_income:
            target = max(savings_pct + 5, 20)
            recs.append(f"Current savings rate is {savings_pct:.1f}%. Consider targeting **{target:.0f}%+** next year.")
        else:
            recs.append("Set your monthly income in Settings to unlock savings-rate recommendations.")
        for r in recs:
            st.markdown(f"- {r}")

# ============================================================================
# PAGE: DATA / EXPORT
# ============================================================================
elif nav == "🗂️ Data / Export":
    st.title("🗂️ Data / Export")

    if df_all.empty:
        st.info("No data yet.")
    else:
        st.markdown(f"**Total records:** {len(df_all)}")
        st.dataframe(df_all.drop(columns=["year", "month", "month_label", "month_name"]),
                     use_container_width=True, hide_index=True)
        csv = df_all.drop(columns=["year", "month", "month_label", "month_name"]).to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download all transactions as CSV", data=csv,
                           file_name="expense_transactions.csv", mime="text/csv")

    st.markdown("---")
    if USING_EXTERNAL_DB:
        st.caption("Connected to an external Postgres database (set via `DATABASE_URL`). "
                   "Data persists across restarts and redeploys.")
    else:
        st.caption("Using a local SQLite file (`expenses.db`) next to this app. "
                   "On hosts with an ephemeral filesystem (e.g. Render's free tier), this "
                   "resets on every restart/redeploy — set `DATABASE_URL` to a free Postgres "
                   "instance to persist data. See README.md.")
