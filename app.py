"""Bundeli Bhaiya - internal wholesale sales tracker.

Logs samosa distributions to local vendors into MongoDB Atlas with a
strict IST timestamp. Laid out for mobile browsers so team members can
add entries from the field.
"""

from datetime import datetime

import pandas as pd
import pymongo
import pytz
import streamlit as st

IST = pytz.timezone("Asia/Kolkata")
DB_NAME = "bundeli_db"
COLLECTION_NAME = "wholesale_logs"
SELECT_PLACEHOLDER = "— pick a vendor —"

st.set_page_config(page_title="Bundeli Bhaiya B2B", page_icon="🥟", layout="centered")


def secret(key, default=None):
    """Read a Streamlit secret without exploding when secrets are absent."""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return default


def gate_passed():
    """Hold the app behind a shared team PIN when APP_PIN is configured.

    A Streamlit Community Cloud URL is public, so anyone who gets the
    link could otherwise write rows into the database. When APP_PIN is
    not set the app stays open, which is fine for local testing.
    """
    expected = secret("APP_PIN")
    if not expected:
        return True
    if st.session_state.get("authenticated"):
        return True

    st.title("🥟 Bundeli Bhaiya")
    st.caption("Internal wholesale tracker")
    entered = st.text_input("Team PIN", type="password")
    if entered:
        if entered == str(expected):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Wrong PIN.")
    return False


@st.cache_resource
def get_collection():
    """Open one pooled Mongo connection and reuse it across reruns."""
    uri = secret("MONGO_URI")
    if not uri:
        st.error("MONGO_URI is not configured. Add it to Streamlit secrets.")
        st.stop()

    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10000)
    collection = client[DB_NAME][COLLECTION_NAME]
    # Keeps the daily log query fast as the collection grows.
    collection.create_index([("date_str", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)])
    return collection


@st.cache_data(ttl=300)
def get_known_vendors():
    """Vendor names already present in the data, for the dropdown."""
    return sorted(get_collection().distinct("vendor_name"))


def log_sale(vendor_name, quantity, rate):
    """Insert one wholesale entry stamped with the current IST time."""
    timestamp = datetime.now(IST)
    record = {
        "vendor_name": vendor_name,
        "quantity": int(quantity),
        "rate": float(rate),
        "total_amount": round(int(quantity) * float(rate), 2),
        # MongoDB stores BSON dates as UTC and drops the offset, so this
        # field reads back as UTC. The three fields below keep IST intact.
        "timestamp": timestamp,
        "timestamp_ist": timestamp.isoformat(),
        "date_str": timestamp.strftime("%Y-%m-%d"),
        "time_str": timestamp.strftime("%I:%M %p"),
    }
    get_collection().insert_one(record)
    get_known_vendors.clear()
    return record


if not gate_passed():
    st.stop()

st.title("🥟 Bundeli Bhaiya — Wholesale")
st.caption("Log daily samosa distributions to local vendors. Times are IST.")

known_vendors = get_known_vendors()

with st.form("sales_form", clear_on_submit=True):
    if known_vendors:
        selected_vendor = st.selectbox(
            "Existing vendor",
            [SELECT_PLACEHOLDER] + known_vendors,
        )
    else:
        selected_vendor = SELECT_PLACEHOLDER

    new_vendor = st.text_input(
        "Or add a new vendor",
        placeholder="e.g. Raju Tea Stall",
        help="Anything typed here takes priority over the dropdown.",
    )

    col_qty, col_rate = st.columns(2)
    with col_qty:
        quantity = st.number_input("Quantity", min_value=1, step=1, value=50)
    with col_rate:
        rate = st.number_input("Rate (₹)", min_value=0.0, step=0.5, value=12.0)

    submitted = st.form_submit_button("Log sale", type="primary")

if submitted:
    vendor = new_vendor.strip() or (
        selected_vendor if selected_vendor != SELECT_PLACEHOLDER else ""
    )
    if not vendor:
        st.error("Pick an existing vendor or type a new vendor name.")
    else:
        try:
            saved = log_sale(vendor, quantity, rate)
        except pymongo.errors.PyMongoError as exc:
            st.error(f"Could not save to the database: {exc}")
        else:
            st.success(
                f"Logged {saved['quantity']} samosas for {saved['vendor_name']} "
                f"at ₹{saved['rate']:g} — total ₹{saved['total_amount']:g}"
            )

st.divider()

today_str = datetime.now(IST).strftime("%Y-%m-%d")
st.subheader(f"Today · {datetime.now(IST).strftime('%d %b %Y')}")

try:
    todays_sales = list(get_collection().find({"date_str": today_str}).sort("timestamp", -1))
except pymongo.errors.PyMongoError as exc:
    st.error(f"Could not read today's logs: {exc}")
    todays_sales = []

if todays_sales:
    frame = pd.DataFrame(todays_sales)

    col_qty_total, col_amount_total, col_vendors = st.columns(3)
    col_qty_total.metric("Samosas", f"{int(frame['quantity'].sum())}")
    col_amount_total.metric("Revenue", f"₹{frame['total_amount'].sum():g}")
    col_vendors.metric("Vendors", f"{frame['vendor_name'].nunique()}")

    table = frame[["time_str", "vendor_name", "quantity", "rate", "total_amount"]].copy()
    table.columns = ["Time", "Vendor", "Qty", "Rate", "Total"]
    st.dataframe(table, hide_index=True)
else:
    st.info("No sales logged yet today.")
