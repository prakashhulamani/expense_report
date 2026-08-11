"""
Personal Expense Tracker
=========================
A comprehensive Streamlit application for daily expense entry with
automatic daily / monthly / yearly analysis, budgets, savings tracking,
fixed vs variable & need vs want breakdowns, recurring expense tracking,
trend charts, and end-of-month / end-of-year narrative reports.

Data is stored in a local SQLite database (expense_tracker.db) so the
same file/app can be used continuously all year round without ever
creating a new spreadsheet.

Run with:
    pip install -r requirements.txt
    streamlit run app.py
"""

import os
import calendar
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "expense_tracker.db")

st.set_page_config(
    page_title="Personal Expense Tracker",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

CATEGORY_MAP = {
    "Groceries": ["Supermarket", "Kirana Store", "Online Grocery", "Bulk Purchase", "Other"],
    "Food / Restaurants": ["Dining Out", "Food Delivery", "Coffee / Tea / Snacks", "Milk", "Office Lunch", "Other"],
    "Vegetables / Fruits": ["Vegetables", "Fruits", "Other"],
    "Utilities": ["Electricity", "Water", "Gas", "Internet / Mobile", "DTH / Cable", "Other"],
    "Rent / Home Loan": ["Rent", "Home Loan EMI", "Maintenance / Society", "Other"],
    "Transportation / Fuel": ["Fuel / Petrol / Diesel", "Cab / Auto / Taxi", "Public Transport", "Parking / Toll", "Other"],
    "Vehicle Maintenance": ["Service", "Repair", "Insurance Renewal", "Accessories", "Other"],
    "Medical": ["Doctor Consultation", "Medicines", "Diagnostic Tests", "Hospitalization", "Health Checkup", "Other"],
    "Education": ["School / College Fees", "Tuition / Coaching", "Books / Study Material", "Online Courses", "Other"],
    "Insurance": ["Life Insurance", "Health Insurance", "Vehicle Insurance", "Other"],
    "Investments": ["Mutual Funds / SIP", "Stocks", "PPF / EPF", "Fixed Deposit", "Gold", "Other"],
    "Shopping": ["Clothing", "Electronics", "Footwear", "Accessories", "Online Shopping", "Other"],
    "Entertainment": ["Movies", "OTT Subscriptions", "Outings", "Events / Concerts", "Games", "Other"],
    "Travel": ["Flights", "Hotels", "Local Sightseeing", "Train / Bus", "Other"],
    "Personal Care": ["Salon / Grooming", "Cosmetics", "Gym / Fitness", "Spa / Wellness", "Other"],
    "Household": ["Cleaning Supplies", "Kitchen Items", "Repairs / Maintenance", "Furniture", "Appliances", "Other"],
    "EMI / Loans": ["Personal Loan", "Credit Card EMI", "Consumer Durable Loan", "Other Loan", "Other"],
    "Bank / Financial Charges": ["Bank Fees", "Credit Card Charges", "Late Fees", "Service Charges", "Other"],
    "Gifts / Donations": ["Gifts", "Charity / Donation", "Religious Offerings", "Other"],
    "Children's Expenses": ["School Supplies", "Toys", "Activities / Classes", "Clothing", "Other"],
    "Family Expenses": ["Parents Support", "Family Events", "Other"],
    "Miscellaneous": ["Uncategorized", "Other"],
}

PAYMENT_METHODS = ["Cash", "Credit Card", "Debit Card", "UPI", "Net Banking", "Wallet", "Other"]
PERSON_OPTIONS = ["Self", "Spouse", "Child", "Parents", "Family (Shared)", "Other"]

DEFAULT_FIXED_CATEGORIES = {
    "Rent / Home Loan", "EMI / Loans", "Insurance", "Utilities",
    "Education", "Investments", "Bank / Financial Charges",
}

MONTH_FMT = "%Y-%m"
DATE_FMT = "%Y-%m-%d"

# --------------------------------------------------------------------------
# DATABASE
# --------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            day TEXT NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT,
            description TEXT,
            payment_method TEXT,
            amount REAL NOT NULL,
            need_want TEXT,
            recurring TEXT,
            person TEXT,
            notes TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            category TEXT NOT NULL,
            budget_amount REAL NOT NULL,
            UNIQUE(month, category)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS income (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL UNIQUE,
            amount REAL NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS category_class (
            category TEXT PRIMARY KEY,
            classification TEXT NOT NULL
        )
    """)
    conn.commit()

    # Seed default fixed/variable classification if empty
    existing = pd.read_sql("SELECT category FROM category_class", conn)["category"].tolist()
    for cat in CATEGORY_MAP.keys():
        if cat not in existing:
            cls = "Fixed" if cat in DEFAULT_FIXED_CATEGORIES else "Variable"
            cur.execute(
                "INSERT OR IGNORE INTO category_class (category, classification) VALUES (?,?)",
                (cat, cls),
            )
    conn.commit()
    conn.close()


def run_query(query, params=None):
    conn = get_conn()
    df = pd.read_sql(query, conn, params=params or [])
    conn.close()
    return df


def execute(query, params=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params or [])
    conn.commit()
    conn.close()


def load_expenses():
    df = run_query("SELECT * FROM expenses ORDER BY date DESC, id DESC")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["month"] = df["date"].dt.strftime(MONTH_FMT)
        df["year"] = df["date"].dt.year
    return df


def load_class_map():
    df = run_query("SELECT * FROM category_class")
    return dict(zip(df["category"], df["classification"]))


def load_budgets():
    return run_query("SELECT * FROM budgets")


def load_income():
    return run_query("SELECT * FROM income")


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------
def money(x):
    try:
        return f"₹{x:,.2f}"
    except (TypeError, ValueError):
        return "₹0.00"


def pct(x):
    try:
        return f"{x:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def month_options(df):
    if df.empty:
        return [datetime.now().strftime(MONTH_FMT)]
    months = sorted(df["month"].unique(), reverse=True)
    return months


def year_options(df):
    if df.empty:
        return [datetime.now().year]
    return sorted(df["year"].unique(), reverse=True)


def get_budget_for(month, category, budgets_df):
    row = budgets_df[(budgets_df["month"] == month) & (budgets_df["category"] == category)]
    if row.empty:
        return 0.0
    return float(row["budget_amount"].iloc[0])


def upsert_budget(month, category, amount):
    execute(
        """INSERT INTO budgets (month, category, budget_amount) VALUES (?,?,?)
           ON CONFLICT(month, category) DO UPDATE SET budget_amount=excluded.budget_amount""",
        (month, category, amount),
    )


def upsert_income(month, amount):
    execute(
        """INSERT INTO income (month, amount) VALUES (?,?)
           ON CONFLICT(month) DO UPDATE SET amount=excluded.amount""",
        (month, amount),
    )


def prev_month_str(month_str):
    d = datetime.strptime(month_str, MONTH_FMT)
    first = d.replace(day=1) - timedelta(days=1)
    return first.strftime(MONTH_FMT)


# --------------------------------------------------------------------------
# INIT
# --------------------------------------------------------------------------
init_db()
df_all = load_expenses()
class_map = load_class_map()
budgets_df = load_budgets()
income_df = load_income()

# --------------------------------------------------------------------------
# SIDEBAR NAVIGATION
# --------------------------------------------------------------------------
st.sidebar.title("💰 Expense Tracker")
page = st.sidebar.radio(
    "Navigate",
    [
        "📝 Add Expense",
        "📋 Transactions",
        "📊 Dashboards",
        "🔍 Deep Analysis",
        "📑 Reports",
        "⚙️ Settings",
    ],
)

st.sidebar.markdown("---")
if not df_all.empty:
    st.sidebar.metric("Total Transactions", len(df_all))
    st.sidebar.metric("Total Recorded", money(df_all["amount"].sum()))
    st.sidebar.caption(f"Data range: {df_all['date'].min().date()} → {df_all['date'].max().date()}")
else:
    st.sidebar.info("No expenses recorded yet. Start by adding one!")

# ==========================================================================
# PAGE 1: ADD EXPENSE
# ==========================================================================
if page == "📝 Add Expense":
    st.title("📝 Add Expense")
    st.caption("Enter today's (or any day's) expense. Summaries update automatically.")

    col1, col2 = st.columns([1, 1])
    with col1:
        entry_date = st.date_input("Date", value=date.today(), key="entry_date")
    with col2:
        st.text_input("Day", value=entry_date.strftime("%A"), disabled=True)

    col3, col4 = st.columns([1, 1])
    with col3:
        category = st.selectbox("Expense Category", list(CATEGORY_MAP.keys()), key="entry_category")
    with col4:
        sub_options = CATEGORY_MAP[category] 
        subcategory = st.selectbox("Expense Sub-Category", sub_options, key="entry_subcategory")
        if subcategory == "Other":
            subcategory = st.text_input("Specify Sub-Category", key="entry_subcategory_custom") or "Other"

    description = st.text_input("Description / Purpose", key="entry_description")

    col5, col6, col7 = st.columns(3)
    with col5:
        payment_method = st.selectbox("Payment Method", PAYMENT_METHODS, key="entry_payment")
    with col6:
        amount = st.number_input("Amount (₹)", min_value=0.0, step=10.0, format="%.2f", key="entry_amount")
    with col7:
        need_want = st.radio("Need / Want", ["Need", "Want"], horizontal=True, key="entry_need_want")

    col8, col9 = st.columns(2)
    with col8:
        recurring = st.radio("Recurring / One-Time", ["Recurring", "One-Time"], horizontal=True, key="entry_recurring")
    with col9:
        person = st.selectbox("Person / Family Member", PERSON_OPTIONS, key="entry_person")
        if person == "Other":
            person = st.text_input("Specify Person", key="entry_person_custom") or "Other"

    notes = st.text_area("Notes", key="entry_notes", height=80)

    if st.button("➕ Add Expense", type="primary", use_container_width=True):
        if amount <= 0:
            st.error("Please enter an amount greater than 0.")
        else:
            execute(
                """INSERT INTO expenses
                   (date, day, category, subcategory, description, payment_method,
                    amount, need_want, recurring, person, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry_date.strftime(DATE_FMT),
                    entry_date.strftime("%A"),
                    category,
                    subcategory,
                    description,
                    payment_method,
                    amount,
                    need_want,
                    recurring,
                    person,
                    notes,
                    datetime.now().isoformat(),
                ),
            )
            st.success(f"Added {money(amount)} under {category} / {subcategory} on {entry_date}.")
            st.rerun()

    st.markdown("---")
    st.subheader("Today's Entries")
    today_str = date.today().strftime(DATE_FMT)
    today_df = df_all[df_all["date"].dt.strftime(DATE_FMT) == today_str] if not df_all.empty else pd.DataFrame()
    if today_df.empty:
        st.info("No expenses logged for today yet.")
    else:
        st.dataframe(
            today_df[["date", "category", "subcategory", "description", "payment_method", "amount", "need_want", "recurring", "person"]],
            use_container_width=True, hide_index=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Today's Total", money(today_df["amount"].sum()))
        c2.metric("Transactions", len(today_df))
        c3.metric("Highest Expense", money(today_df["amount"].max()))

# ==========================================================================
# PAGE 2: TRANSACTIONS (view / edit / delete)
# ==========================================================================
elif page == "📋 Transactions":
    st.title("📋 All Transactions")

    if df_all.empty:
        st.info("No expenses recorded yet.")
    else:
        fcol1, fcol2, fcol3, fcol4 = st.columns(4)
        with fcol1:
            f_month = st.multiselect("Month", month_options(df_all))
        with fcol2:
            f_cat = st.multiselect("Category", sorted(df_all["category"].unique()))
        with fcol3:
            f_person = st.multiselect("Person", sorted(df_all["person"].dropna().unique()))
        with fcol4:
            f_needwant = st.multiselect("Need / Want", ["Need", "Want"])

        filtered = df_all.copy()
        if f_month:
            filtered = filtered[filtered["month"].isin(f_month)]
        if f_cat:
            filtered = filtered[filtered["category"].isin(f_cat)]
        if f_person:
            filtered = filtered[filtered["person"].isin(f_person)]
        if f_needwant:
            filtered = filtered[filtered["need_want"].isin(f_needwant)]

        st.dataframe(
            filtered[["id", "date", "day", "category", "subcategory", "description",
                      "payment_method", "amount", "need_want", "recurring", "person", "notes"]],
            use_container_width=True, hide_index=True, height=420,
        )
        st.caption(f"Showing {len(filtered)} of {len(df_all)} transactions — Total: {money(filtered['amount'].sum())}")

        st.markdown("---")
        st.subheader("Delete a Transaction")
        del_id = st.number_input("Transaction ID to delete", min_value=0, step=1)
        if st.button("🗑️ Delete", type="secondary"):
            if del_id > 0:
                execute("DELETE FROM expenses WHERE id=?", (int(del_id),))
                st.success(f"Deleted transaction #{int(del_id)}.")
                st.rerun()

        st.markdown("---")
        st.subheader("Export")
        csv = df_all.drop(columns=["month", "year"]).to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download all data as CSV", csv, "expenses_export.csv", "text/csv")

# ==========================================================================
# PAGE 3: DASHBOARDS  (Daily / Monthly / Yearly)
# ==========================================================================
elif page == "📊 Dashboards":
    st.title("📊 Dashboards")
    tab_daily, tab_monthly, tab_yearly = st.tabs(["📅 Daily", "🗓️ Monthly", "📆 Yearly"])

    # ---------------- DAILY ----------------
    with tab_daily:
        if df_all.empty:
            st.info("No data yet.")
        else:
            sel_date = st.date_input("Select date", value=df_all["date"].max().date(), key="daily_sel")
            day_df = df_all[df_all["date"].dt.date == sel_date]
            if day_df.empty:
                st.warning("No expenses for this date.")
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Expense", money(day_df["amount"].sum()))
                c2.metric("Transactions", len(day_df))
                c3.metric("Highest Expense", money(day_df["amount"].max()))
                c4.metric("Day", sel_date.strftime("%A"))

                colA, colB = st.columns(2)
                with colA:
                    st.markdown("**Category-wise Spending**")
                    cat_sum = day_df.groupby("category")["amount"].sum().sort_values(ascending=False)
                    st.dataframe(cat_sum.reset_index().rename(columns={"amount": "Amount"}), hide_index=True, use_container_width=True)
                with colB:
                    st.markdown("**Need vs Want / Recurring vs One-Time**")
                    nw = day_df.groupby("need_want")["amount"].sum()
                    rc = day_df.groupby("recurring")["amount"].sum()
                    st.write("Need vs Want:", {k: money(v) for k, v in nw.items()})
                    st.write("Recurring vs One-Time:", {k: money(v) for k, v in rc.items()})

                st.dataframe(day_df[["category", "subcategory", "description", "amount", "payment_method", "person"]],
                             hide_index=True, use_container_width=True)

    # ---------------- MONTHLY ----------------
    with tab_monthly:
        if df_all.empty:
            st.info("No data yet.")
        else:
            sel_month = st.selectbox("Select month", month_options(df_all), key="monthly_sel")
            mdf = df_all[df_all["month"] == sel_month]
            prev_m = prev_month_str(sel_month)
            pmdf = df_all[df_all["month"] == prev_m]

            total_month = mdf["amount"].sum()
            days_in_month = mdf["date"].dt.day.max() if not mdf.empty else 1
            avg_daily = total_month / max(days_in_month, 1)
            prev_total = pmdf["amount"].sum()
            mom_change = ((total_month - prev_total) / prev_total * 100) if prev_total else None

            budget_total = get_budget_for(sel_month, "__TOTAL__", budgets_df)
            income_row = income_df[income_df["month"] == sel_month]
            income_amt = float(income_row["amount"].iloc[0]) if not income_row.empty else 0.0
            savings_amt = income_amt - total_month
            savings_pct = (savings_amt / income_amt * 100) if income_amt else None

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Monthly Expense", money(total_month))
            c2.metric("Avg Daily Expense", money(avg_daily))
            c3.metric("Transactions", len(mdf))
            c4.metric("MoM Change", pct(mom_change) if mom_change is not None else "N/A",
                      delta=pct(mom_change) if mom_change is not None else None)

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Budget", money(budget_total) if budget_total else "Not set")
            c6.metric("Remaining Budget", money(budget_total - total_month) if budget_total else "N/A")
            c7.metric("Savings", money(savings_amt) if income_amt else "Set income →")
            c8.metric("Savings %", pct(savings_pct) if savings_pct is not None else "N/A")

            if not mdf.empty:
                cat_sum = mdf.groupby("category")["amount"].sum().sort_values(ascending=False)
                top_cat = cat_sum.index[0]
                day_sum = mdf.groupby(mdf["date"].dt.date)["amount"].sum()
                top_day = day_sum.idxmax()

                colA, colB = st.columns(2)
                colA.metric("Highest Spending Category", f"{top_cat} ({money(cat_sum.iloc[0])})")
                colB.metric("Highest Spending Day", f"{top_day} ({money(day_sum.max())})")

                st.markdown("**Category-wise Monthly Expense & % Share**")
                cat_table = cat_sum.reset_index().rename(columns={"amount": "Amount"})
                cat_table["% of Total"] = (cat_table["Amount"] / total_month * 100).round(1)
                st.dataframe(cat_table, hide_index=True, use_container_width=True)

                fixvar = mdf.copy()
                fixvar["classification"] = fixvar["category"].map(class_map).fillna("Variable")
                colC, colD, colE = st.columns(3)
                with colC:
                    st.markdown("**Fixed vs Variable**")
                    st.write({k: money(v) for k, v in fixvar.groupby("classification")["amount"].sum().items()})
                with colD:
                    st.markdown("**Need vs Want**")
                    st.write({k: money(v) for k, v in mdf.groupby("need_want")["amount"].sum().items()})
                with colE:
                    st.markdown("**Recurring vs One-Time**")
                    st.write({k: money(v) for k, v in mdf.groupby("recurring")["amount"].sum().items()})

    # ---------------- YEARLY ----------------
    with tab_yearly:
        if df_all.empty:
            st.info("No data yet.")
        else:
            sel_year = st.selectbox("Select year", year_options(df_all), key="yearly_sel")
            ydf = df_all[df_all["year"] == sel_year]

            total_year = ydf["amount"].sum()
            months_present = ydf["month"].nunique()
            avg_monthly = total_year / max(months_present, 1)
            days_present = ydf["date"].dt.date.nunique()
            avg_daily_y = total_year / max(days_present, 1)

            budget_year = sum(
                get_budget_for(m, "__TOTAL__", budgets_df)
                for m in sorted(ydf["month"].unique())
            )
            income_year = income_df[income_df["month"].isin(ydf["month"].unique())]["amount"].sum()
            savings_year = income_year - total_year
            savings_pct_y = (savings_year / income_year * 100) if income_year else None

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Annual Expense", money(total_year))
            c2.metric("Avg Monthly Expense", money(avg_monthly))
            c3.metric("Avg Daily Expense", money(avg_daily_y))
            c4.metric("Transactions", len(ydf))

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Annual Budget", money(budget_year) if budget_year else "Not set")
            c6.metric("Budget vs Actual", money(budget_year - total_year) if budget_year else "N/A")
            c7.metric("Total Savings", money(savings_year) if income_year else "Set income →")
            c8.metric("Savings %", pct(savings_pct_y) if savings_pct_y is not None else "N/A")

            monthly_trend = ydf.groupby("month")["amount"].sum().sort_index()
            if not monthly_trend.empty:
                highest_month = monthly_trend.idxmax()
                lowest_month = monthly_trend.idxmin()
                colA, colB = st.columns(2)
                colA.metric("Highest Spending Month", f"{highest_month} ({money(monthly_trend.max())})")
                colB.metric("Lowest Spending Month", f"{lowest_month} ({money(monthly_trend.min())})")

                # Month-over-month growth
                mom_growth = monthly_trend.pct_change().fillna(0) * 100
                st.markdown("**Monthly Trend & MoM Growth**")
                trend_table = pd.DataFrame({
                    "Month": monthly_trend.index,
                    "Expense": monthly_trend.values,
                    "MoM Growth %": mom_growth.round(1).values,
                })
                st.dataframe(trend_table, hide_index=True, use_container_width=True)

                fig = px.line(trend_table, x="Month", y="Expense", markers=True, title="Monthly Expense Trend")
                st.plotly_chart(fig, use_container_width=True)

            cat_sum_y = ydf.groupby("category")["amount"].sum().sort_values(ascending=False)
            if not cat_sum_y.empty:
                st.metric("Highest Spending Category (Year)", f"{cat_sum_y.index[0]} ({money(cat_sum_y.iloc[0])})")

            # Year-to-date & projection
            today = date.today()
            if sel_year == today.year:
                ytd = ydf[ydf["date"].dt.date <= today]["amount"].sum()
                days_elapsed = (today - date(today.year, 1, 1)).days + 1
                projected = ytd / max(days_elapsed, 1) * 365
                c9, c10 = st.columns(2)
                c9.metric("Year-to-Date Expense", money(ytd))
                c10.metric("Projected Annual Expense", money(projected))

# ==========================================================================
# PAGE 4: DEEP ANALYSIS
# ==========================================================================
elif page == "🔍 Deep Analysis":
    st.title("🔍 Deep Analysis")

    if df_all.empty:
        st.info("No data yet.")
    else:
        (tab_cat, tab_budget, tab_savings, tab_fixvar,
         tab_needwant, tab_recurring, tab_trend) = st.tabs(
            ["🏷️ Category", "💰 Budget vs Actual", "🐷 Savings",
             "🏠 Fixed vs Variable", "🎯 Need vs Want", "🔁 Recurring", "📈 Trends"]
        )

        # ---- Category Analysis ----
        with tab_cat:
            scope = st.radio("Scope", ["All Time", "By Month", "By Year"], horizontal=True, key="cat_scope")
            cdf = df_all
            if scope == "By Month":
                m = st.selectbox("Month", month_options(df_all), key="cat_month")
                cdf = df_all[df_all["month"] == m]
            elif scope == "By Year":
                y = st.selectbox("Year", year_options(df_all), key="cat_year")
                cdf = df_all[df_all["year"] == y]

            cat_sum = cdf.groupby("category")["amount"].sum().sort_values(ascending=False)
            fig1 = px.pie(values=cat_sum.values, names=cat_sum.index, title="Category-wise Expense Distribution", hole=0.4)
            st.plotly_chart(fig1, use_container_width=True)

            top10 = cat_sum.head(10)
            fig2 = px.bar(x=top10.values, y=top10.index, orientation="h", title="Top 10 Expense Categories",
                          labels={"x": "Amount", "y": "Category"})
            fig2.update_yaxes(autorange="reversed")
            st.plotly_chart(fig2, use_container_width=True)

            st.markdown("**Sub-Category Breakdown**")
            sub_sum = cdf.groupby(["category", "subcategory"])["amount"].sum().sort_values(ascending=False).reset_index()
            st.dataframe(sub_sum.rename(columns={"amount": "Amount"}), hide_index=True, use_container_width=True, height=300)

        # ---- Budget vs Actual ----
        with tab_budget:
            b_month = st.selectbox("Month", month_options(df_all), key="budget_month")
            st.markdown("#### Set / Update Budgets")
            bcol1, bcol2, bcol3 = st.columns(3)
            with bcol1:
                total_budget_val = get_budget_for(b_month, "__TOTAL__", load_budgets())
                new_total_budget = st.number_input("Overall Monthly Budget (₹)", min_value=0.0, step=500.0,
                                                     value=float(total_budget_val), key="total_budget_input")
            with bcol2:
                b_cat = st.selectbox("Category", list(CATEGORY_MAP.keys()), key="budget_cat")
            with bcol3:
                cat_budget_val = get_budget_for(b_month, b_cat, load_budgets())
                new_cat_budget = st.number_input(f"Budget for {b_cat} (₹)", min_value=0.0, step=100.0,
                                                   value=float(cat_budget_val), key="cat_budget_input")
            if st.button("💾 Save Budgets"):
                upsert_budget(b_month, "__TOTAL__", new_total_budget)
                upsert_budget(b_month, b_cat, new_cat_budget)
                st.success("Budgets saved.")
                st.rerun()

            st.markdown("---")
            bdf = df_all[df_all["month"] == b_month]
            actual_total = bdf["amount"].sum()
            budget_now = get_budget_for(b_month, "__TOTAL__", load_budgets())
            c1, c2, c3 = st.columns(3)
            c1.metric("Budget", money(budget_now))
            c2.metric("Actual", money(actual_total))
            c3.metric("Remaining", money(budget_now - actual_total))

            budgets_now = load_budgets()
            budgets_now = budgets_now[(budgets_now["month"] == b_month) & (budgets_now["category"] != "__TOTAL__")]
            actual_by_cat = bdf.groupby("category")["amount"].sum()
            comp = budgets_now.set_index("category")["budget_amount"].to_frame("Budget")
            comp["Actual"] = comp.index.map(lambda c: actual_by_cat.get(c, 0))
            comp["Remaining"] = comp["Budget"] - comp["Actual"]
            if not comp.empty:
                st.dataframe(comp.reset_index(), hide_index=True, use_container_width=True)
                fig = go.Figure()
                fig.add_bar(name="Budget", x=comp.index, y=comp["Budget"])
                fig.add_bar(name="Actual", x=comp.index, y=comp["Actual"])
                fig.update_layout(barmode="group", title="Budget vs Actual by Category")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No category budgets set for this month yet.")

        # ---- Savings ----
        with tab_savings:
            s_month = st.selectbox("Month", month_options(df_all), key="savings_month")
            income_row = load_income()
            income_row = income_row[income_row["month"] == s_month]
            income_val = float(income_row["amount"].iloc[0]) if not income_row.empty else 0.0
            new_income = st.number_input("Monthly Income (₹)", min_value=0.0, step=1000.0, value=income_val, key="income_input")
            if st.button("💾 Save Income"):
                upsert_income(s_month, new_income)
                st.success("Income saved.")
                st.rerun()

            sdf = df_all[df_all["month"] == s_month]
            expense_total = sdf["amount"].sum()
            savings = new_income - expense_total
            savings_pct_val = (savings / new_income * 100) if new_income else None
            c1, c2, c3 = st.columns(3)
            c1.metric("Income", money(new_income))
            c2.metric("Expense", money(expense_total))
            c3.metric("Savings", money(savings))
            st.metric("Savings %", pct(savings_pct_val) if savings_pct_val is not None else "N/A")

            st.markdown("**Monthly Savings Trend**")
            inc_all = load_income().set_index("month")["amount"]
            exp_all = df_all.groupby("month")["amount"].sum()
            trend = pd.DataFrame({"Income": inc_all, "Expense": exp_all}).fillna(0)
            trend["Savings"] = trend["Income"] - trend["Expense"]
            trend = trend.sort_index()
            if not trend.empty:
                fig = px.line(trend.reset_index(), x="month", y="Savings", markers=True, title="Monthly Savings Trend")
                st.plotly_chart(fig, use_container_width=True)

        # ---- Fixed vs Variable ----
        with tab_fixvar:
            fv = df_all.copy()
            fv["classification"] = fv["category"].map(class_map).fillna("Variable")
            fscope = st.radio("Scope", ["All Time", "By Month", "By Year"], horizontal=True, key="fv_scope")
            if fscope == "By Month":
                m = st.selectbox("Month", month_options(df_all), key="fv_month")
                fv = fv[fv["month"] == m]
            elif fscope == "By Year":
                y = st.selectbox("Year", year_options(df_all), key="fv_year")
                fv = fv[fv["year"] == y]
            fv_sum = fv.groupby("classification")["amount"].sum()
            fig = px.pie(values=fv_sum.values, names=fv_sum.index, title="Fixed vs Variable Expenses", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(fv_sum.reset_index().rename(columns={"amount": "Amount"}), hide_index=True, use_container_width=True)

        # ---- Need vs Want ----
        with tab_needwant:
            nwscope = st.radio("Scope", ["All Time", "By Month", "By Year"], horizontal=True, key="nw_scope")
            nw = df_all
            if nwscope == "By Month":
                m = st.selectbox("Month", month_options(df_all), key="nw_month")
                nw = df_all[df_all["month"] == m]
            elif nwscope == "By Year":
                y = st.selectbox("Year", year_options(df_all), key="nw_year")
                nw = df_all[df_all["year"] == y]
            nw_sum = nw.groupby("need_want")["amount"].sum()
            fig = px.pie(values=nw_sum.values, names=nw_sum.index, title="Need vs Want", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(nw_sum.reset_index().rename(columns={"amount": "Amount"}), hide_index=True, use_container_width=True)

        # ---- Recurring Tracker ----
        with tab_recurring:
            rec_df = df_all[df_all["recurring"] == "Recurring"]
            if rec_df.empty:
                st.info("No recurring expenses logged yet.")
            else:
                st.markdown("**Recurring Expenses by Category / Sub-Category (all time)**")
                rec_sum = rec_df.groupby(["category", "subcategory"])["amount"].agg(["sum", "count", "mean"]).reset_index()
                rec_sum.columns = ["Category", "Sub-Category", "Total Spent", "Occurrences", "Avg Amount"]
                rec_sum = rec_sum.sort_values("Total Spent", ascending=False)
                st.dataframe(rec_sum, hide_index=True, use_container_width=True)

                st.markdown("**Recurring vs One-Time — Monthly Comparison**")
                rc_trend = df_all.groupby(["month", "recurring"])["amount"].sum().unstack(fill_value=0).sort_index()
                if not rc_trend.empty:
                    fig = px.bar(rc_trend.reset_index(), x="month", y=rc_trend.columns.tolist(),
                                 title="Recurring vs One-Time Expenses by Month", barmode="stack")
                    st.plotly_chart(fig, use_container_width=True)

                st.caption("💡 Review recurring items with high 'Total Spent' — subscriptions or plans here are prime candidates for reduction.")

        # ---- Trends ----
        with tab_trend:
            monthly_trend = df_all.groupby("month")["amount"].sum().sort_index().reset_index()
            fig1 = px.line(monthly_trend, x="month", y="amount", markers=True, title="Monthly Expense Trend (All Time)")
            st.plotly_chart(fig1, use_container_width=True)

            cat_month = df_all.groupby(["month", "category"])["amount"].sum().reset_index()
            fig2 = px.bar(cat_month, x="month", y="amount", color="category", title="Category-wise Monthly Trend")
            st.plotly_chart(fig2, use_container_width=True)

            payment_sum = df_all.groupby("payment_method")["amount"].sum().sort_values(ascending=False)
            fig3 = px.bar(x=payment_sum.index, y=payment_sum.values, title="Spending by Payment Method",
                          labels={"x": "Payment Method", "y": "Amount"})
            st.plotly_chart(fig3, use_container_width=True)

# ==========================================================================
# PAGE 5: REPORTS (Month-End / Year-End narrative summaries)
# ==========================================================================
elif page == "📑 Reports":
    st.title("📑 Reports")
    tab_month_end, tab_year_end = st.tabs(["🗓️ Month-End Summary", "🎊 Year-End Review"])

    with tab_month_end:
        if df_all.empty:
            st.info("No data yet.")
        else:
            m = st.selectbox("Select month", month_options(df_all), key="report_month")
            mdf = df_all[df_all["month"] == m]
            prev_m = prev_month_str(m)
            pmdf = df_all[df_all["month"] == prev_m]

            total_month = mdf["amount"].sum()
            budget_total = get_budget_for(m, "__TOTAL__", budgets_df)
            income_row = income_df[income_df["month"] == m]
            income_amt = float(income_row["amount"].iloc[0]) if not income_row.empty else 0.0
            savings_amt = income_amt - total_month

            cat_now = mdf.groupby("category")["amount"].sum()
            cat_prev = pmdf.groupby("category")["amount"].sum()
            all_cats_idx = cat_now.index.union(cat_prev.index)
            diff = cat_now.reindex(all_cats_idx).fillna(0) - cat_prev.reindex(all_cats_idx).fillna(0)
            increased = diff[diff > 0].sort_values(ascending=False)
            decreased = diff[diff < 0].sort_values()

            wants_df = mdf[mdf["need_want"] == "Want"].sort_values("amount", ascending=False)
            recurring_cat = mdf[mdf["recurring"] == "Recurring"].groupby("category")["amount"].sum().sort_values(ascending=False)

            st.markdown(f"## Financial Summary — {m}")
            st.markdown(f"**1. How much did I spend?**  {money(total_month)} across {len(mdf)} transactions.")

            if not cat_now.empty:
                st.markdown(f"**2. Where did I spend the most?**  {cat_now.idxmax()} ({money(cat_now.max())}, "
                            f"{cat_now.max()/total_month*100:.1f}% of the month).")
            else:
                st.markdown("**2. Where did I spend the most?**  No data.")

            st.markdown("**3. Which expenses increased (vs previous month)?**")
            if increased.empty:
                st.write("- No category increased vs last month.")
            else:
                for c, v in increased.head(5).items():
                    st.write(f"- {c}: +{money(v)}")

            st.markdown("**4. Which expenses decreased (vs previous month)?**")
            if decreased.empty:
                st.write("- No category decreased vs last month.")
            else:
                for c, v in decreased.head(5).items():
                    st.write(f"- {c}: {money(v)}")

            st.markdown("**5. What were my unnecessary (Want) expenses?**")
            if wants_df.empty:
                st.write("- None logged as 'Want' this month.")
            else:
                st.write(f"- Total 'Want' spending: {money(wants_df['amount'].sum())} "
                         f"({wants_df['amount'].sum()/total_month*100:.1f}% of month)")
                st.dataframe(wants_df[["date", "category", "subcategory", "description", "amount"]].head(10),
                             hide_index=True, use_container_width=True)

            st.markdown("**6. What recurring expenses can be reduced?**")
            if recurring_cat.empty:
                st.write("- No recurring expenses logged this month.")
            else:
                st.dataframe(recurring_cat.reset_index().rename(columns={"amount": "Amount"}).head(10),
                             hide_index=True, use_container_width=True)

            st.markdown(f"**7. How much did I save?**  "
                        f"{money(savings_amt) if income_amt else 'Set income in Deep Analysis → Savings tab to compute.'}")

            if budget_total:
                status = "✅ Within budget" if total_month <= budget_total else "⚠️ Over budget"
                st.markdown(f"**8. Did I stay within my budget?**  {status} — Budget {money(budget_total)}, "
                            f"Actual {money(total_month)}, Difference {money(budget_total - total_month)}.")
            else:
                st.markdown("**8. Did I stay within my budget?**  No budget set for this month.")

            st.markdown("**9. What should I improve next month?**")
            tips = []
            if not increased.empty:
                tips.append(f"Watch {increased.index[0]} — it rose the most vs last month.")
            if not wants_df.empty and wants_df["amount"].sum() / total_month > 0.3:
                tips.append("'Want' spending is over 30% of the month — consider trimming discretionary spend.")
            if budget_total and total_month > budget_total:
                tips.append("Overall spending exceeded budget — revisit category budgets or spending pace.")
            if not tips:
                tips.append("Spending looks steady — keep tracking consistently and consider raising savings rate.")
            for t in tips:
                st.write(f"- {t}")

    with tab_year_end:
        if df_all.empty:
            st.info("No data yet.")
        else:
            y = st.selectbox("Select year", year_options(df_all), key="report_year")
            ydf = df_all[df_all["year"] == y]

            total_expense = ydf["amount"].sum()
            income_year = income_df[income_df["month"].isin(ydf["month"].unique())]["amount"].sum()
            savings_year = income_year - total_expense
            savings_pct_y = (savings_year / income_year * 100) if income_year else None
            invest_total = ydf[ydf["category"] == "Investments"]["amount"].sum()

            fv = ydf.copy()
            fv["classification"] = fv["category"].map(class_map).fillna("Variable")
            fixed_total = fv[fv["classification"] == "Fixed"]["amount"].sum()
            variable_total = fv[fv["classification"] == "Variable"]["amount"].sum()

            cat_sum = ydf.groupby("category")["amount"].sum().sort_values(ascending=False)
            monthly_trend = ydf.groupby("month")["amount"].sum().sort_index()
            recurring_sum = ydf[ydf["recurring"] == "Recurring"].groupby("category")["amount"].sum().sort_values(ascending=False)
            wants_top = ydf[ydf["need_want"] == "Want"].sort_values("amount", ascending=False).head(10)
            budget_year = sum(get_budget_for(m, "__TOTAL__", budgets_df) for m in ydf["month"].unique())

            st.markdown(f"## Annual Financial Review — {y}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Income", money(income_year) if income_year else "Not set")
            c2.metric("Total Expenses", money(total_expense))
            c3.metric("Total Savings", money(savings_year) if income_year else "N/A")

            c4, c5, c6 = st.columns(3)
            c4.metric("Savings %", pct(savings_pct_y) if savings_pct_y is not None else "N/A")
            c5.metric("Total Investments", money(invest_total))
            c6.metric("Budget Performance", money(budget_year - total_expense) if budget_year else "No budget set")

            c7, c8 = st.columns(2)
            c7.metric("Total Fixed Expenses", money(fixed_total))
            c8.metric("Total Variable Expenses", money(variable_total))

            st.markdown("### Top 10 Expense Categories")
            st.dataframe(cat_sum.head(10).reset_index().rename(columns={"amount": "Amount"}),
                         hide_index=True, use_container_width=True)

            st.markdown("### Highest Spending Months")
            st.dataframe(monthly_trend.sort_values(ascending=False).head(3).reset_index()
                         .rename(columns={"month": "Month", "amount": "Amount"}),
                         hide_index=True, use_container_width=True)

            st.markdown("### Biggest Unnecessary (Want) Expenses")
            if wants_top.empty:
                st.write("None logged.")
            else:
                st.dataframe(wants_top[["date", "category", "description", "amount"]],
                             hide_index=True, use_container_width=True)

            st.markdown("### Recurring Expenses (by category)")
            if recurring_sum.empty:
                st.write("None logged.")
            else:
                st.dataframe(recurring_sum.reset_index().rename(columns={"amount": "Amount"}),
                             hide_index=True, use_container_width=True)

            st.markdown("### Monthly Spending Trend")
            fig = px.line(monthly_trend.reset_index(), x="month", y="amount", markers=True)
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("### Recommended Areas for Expense Reduction")
            top_want_cats = ydf[ydf["need_want"] == "Want"].groupby("category")["amount"].sum().sort_values(ascending=False).head(3)
            for c, v in top_want_cats.items():
                st.write(f"- **{c}**: {money(v)} in discretionary ('Want') spending — review for cuts.")
            if recurring_sum.shape[0] > 0:
                st.write(f"- **{recurring_sum.index[0]}**: highest recurring spend ({money(recurring_sum.iloc[0])}) — "
                         f"check for unused subscriptions or renegotiation options.")

            st.markdown("### Recommended Savings Target for Next Year")
            if income_year:
                current_rate = savings_pct_y or 0
                target_rate = min(current_rate + 5, 40)
                st.write(f"Current savings rate: **{pct(current_rate)}**. "
                         f"Suggested target for next year: **{pct(target_rate)}** "
                         f"(≈ {money(income_year * target_rate / 100)} if income stays similar).")
            else:
                st.write("Add monthly income figures (Deep Analysis → Savings tab) to get a personalized savings target.")

# ==========================================================================
# PAGE 6: SETTINGS
# ==========================================================================
elif page == "⚙️ Settings":
    st.title("⚙️ Settings")

    st.subheader("Fixed vs Variable Category Classification")
    st.caption("Used to compute Fixed vs Variable dashboards. Defaults are pre-set — adjust as needed.")
    cls_df = run_query("SELECT * FROM category_class ORDER BY category")
    updated = {}
    cols = st.columns(2)
    for i, row in cls_df.iterrows():
        with cols[i % 2]:
            val = st.radio(row["category"], ["Fixed", "Variable"],
                            index=0 if row["classification"] == "Fixed" else 1,
                            key=f"cls_{row['category']}", horizontal=True)
            updated[row["category"]] = val
    if st.button("💾 Save Classification"):
        for cat, val in updated.items():
            execute("UPDATE category_class SET classification=? WHERE category=?", (val, cat))
        st.success("Saved.")
        st.rerun()

    st.markdown("---")
    st.subheader("Database Backup")
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "rb") as f:
            st.download_button("⬇️ Download database file (expense_tracker.db)", f, "expense_tracker.db")
    st.caption(f"Database location: `{DB_PATH}` — this single file holds all your data for every month and year.")

    st.markdown("---")
    st.subheader("Import Expenses from CSV")
    st.caption("CSV columns expected: date, category, subcategory, description, payment_method, amount, need_want, recurring, person, notes")
    uploaded = st.file_uploader("Upload CSV", type="csv")
    if uploaded is not None:
        try:
            imp_df = pd.read_csv(uploaded)
            imp_df["date"] = pd.to_datetime(imp_df["date"])
            if st.button("Import rows"):
                conn = get_conn()
                cur = conn.cursor()
                for _, r in imp_df.iterrows():
                    cur.execute(
                        """INSERT INTO expenses
                           (date, day, category, subcategory, description, payment_method,
                            amount, need_want, recurring, person, notes, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            r["date"].strftime(DATE_FMT), r["date"].strftime("%A"),
                            r.get("category", "Miscellaneous"), r.get("subcategory", "Other"),
                            r.get("description", ""), r.get("payment_method", "Other"),
                            float(r.get("amount", 0)), r.get("need_want", "Need"),
                            r.get("recurring", "One-Time"), r.get("person", "Self"),
                            r.get("notes", ""), datetime.now().isoformat(),
                        ),
                    )
                conn.commit()
                conn.close()
                st.success(f"Imported {len(imp_df)} rows.")
                st.rerun()
        except Exception as e:
            st.error(f"Could not parse CSV: {e}")
