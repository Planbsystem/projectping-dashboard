import os
import pandas as pd
import streamlit as st
import altair as alt

# -------------------- Page setup --------------------
st.set_page_config(page_title="ProjectPing Dashboard", layout="wide")
st.title("ProjectPing Dashboard")

# Auto-refresh (no external package)
REFRESH_SEC = int(os.environ.get("REFRESH_SEC", "60"))
st.markdown(f"<meta http-equiv='refresh' content='{REFRESH_SEC}'>", unsafe_allow_html=True)

# -------------------- Data loading --------------------
@st.cache_data(ttl=REFRESH_SEC, show_spinner=False)
def load_csv(url: str) -> pd.DataFrame:
    return pd.read_csv(url)

csv_url = os.environ.get("SHEET_CSV_URL", "").strip()
if not csv_url:
    st.warning("ยังไม่ตั้งค่า SHEET_CSV_URL (ลิงก์ Publish-to-Web แบบ CSV ของชีท Data)")
    st.info('ไปที่ Manage app → Settings → Secrets แล้วใส่ในรูปแบบ TOML:\n\n'
            'SHEET_CSV_URL = "https://.../pub?gid=XXXX&single=true&output=csv"\n'
            'REFRESH_SEC   = "60"')
    st.stop()

try:
    df = load_csv(csv_url)
except Exception as e:
    st.error("โหลด CSV ไม่ได้ — ตรวจสอบว่าเป็นลิงก์ Publish to web (CSV) ของชีท Data")
    st.exception(e)
    st.stop()

# -------------------- Timestamp handling --------------------
def ensure_timestamp(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create/normalize a 'Timestamp' column as pandas datetime64[ns].
    Tries the following in order:
      1) If 'Date' + 'Time' exist -> combine
      2) If 'Timestamp' exists -> parse to datetime
      3) If only 'Date' exists -> parse 'Date' as timestamp (time = 00:00:00)
      4) Try to infer from the first string-like column that parses best
    """
    df = df.copy()

    def _to_datetime_series(s, time_fmt=None):
        # Helper to robustly parse a series to datetime
        if time_fmt:
            try:
                # For 'Time' column formatted like "13:05:33"
                t = pd.to_datetime(s.astype(str), format=time_fmt, errors="coerce").dt.time
                return t
            except Exception:
                return pd.to_datetime(s, errors="coerce")
        return pd.to_datetime(s, errors="coerce")

    cols = set(df.columns)

    if {"Date", "Time"}.issubset(cols):
        d = _to_datetime_series(df["Date"])
        # if Time is HH:MM:SS, parse with format for speed; otherwise coerce
        try:
            t = pd.to_datetime(df["Time"].astype(str), format="%H:%M:%S", errors="coerce").dt.time
        except Exception:
            t = _to_datetime_series(df["Time"]).dt.time
        df["Timestamp"] = pd.to_datetime(d.astype(str) + " " + pd.Series(t, dtype="object").astype(str),
                                         errors="coerce")

    elif "Timestamp" in cols:
        df["Timestamp"] = _to_datetime_series(df["Timestamp"])

    elif "Date" in cols:
        df["Timestamp"] = _to_datetime_series(df["Date"])

    else:
        # Fallback: try parse each object column, pick the one with most valid datetimes
        candidate = None
        best_non_na = -1
        for c in df.columns:
            if df[c].dtype == "object":
                parsed = pd.to_datetime(df[c], errors="coerce")
                non_na = parsed.notna().sum()
                if non_na > best_non_na and non_na > 0:
                    best_non_na = non_na
                    candidate = c
        if candidate is not None:
            df["Timestamp"] = pd.to_datetime(df[candidate], errors="coerce")
        else:
            # create an empty column to avoid key errors later
            df["Timestamp"] = pd.NaT

    return df

df = ensure_timestamp(df)

# Guard: if still no usable Timestamp, show guide and stop
if "Timestamp" not in df.columns or df["Timestamp"].notna().sum() == 0:
    st.error("ไม่พบข้อมูลเวลา (Timestamp) ที่ใช้งานได้ใน CSV")
    st.info("ตรวจดูว่า CSV มีคอลัมน์อย่างน้อยหนึ่งในนี้: "
            "`Date`+`Time`, `Timestamp`, หรือ `Date`")
    st.stop()

# -------------------- Sidebar filters --------------------
with st.sidebar:
    st.header("Filters")
    date_range = st.selectbox("Date Range", ["Last 7 days", "Today", "Last 24 hours", "Custom"])

    proj_opts = sorted(df["Project"].dropna().unique()) if "Project" in df.columns else []
    type_opts = sorted(df["DeviceType"].dropna().unique()) if "DeviceType" in df.columns else []
    status_opts = ["ONLINE", "OFFLINE", "HIGH LOSS", "UNKNOWN"] if "PingStatus_Calculated" in df.columns else []

    projects = st.multiselect("Project", proj_opts, default=proj_opts if proj_opts else [])
    device_types = st.multiselect("Device Type", type_opts, default=type_opts if type_opts else [])
    statuses = st.multiselect("Connection Status", status_opts, default=status_opts if status_opts else [])

# -------------------- Date filtering --------------------
now = pd.Timestamp.utcnow()

if date_range == "Last 7 days":
    start = now - pd.Timedelta(days=7)
elif date_range == "Today":
    start = pd.Timestamp(pd.Timestamp.utcnow().date())
elif date_range == "Last 24 hours":
    start = now - pd.Timedelta(hours=24)
else:  # Custom
    c = st.date_input("Select date range", value=(pd.Timestamp.now().date(), pd.Timestamp.now().date()))
    if isinstance(c, tuple) and len(c) == 2:
        start = pd.Timestamp(c[0])
        now = pd.Timestamp(c[1]) + pd.Timedelta(days=1)
    else:
        start = now - pd.Timedelta(days=7)

# make sure Timestamp is datetime before compare
df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
df = df[(df["Timestamp"] >= start) & (df["Timestamp"] <= now)].copy()

# -------------------- Other filters --------------------
if "Project" in df.columns and projects:
    df = df[df["Project"].isin(projects)]
if "DeviceType" in df.columns and device_types:
    df = df[df["DeviceType"].isin(device_types)]
if "PingStatus_Calculated" in df.columns and statuses:
    df = df[df["PingStatus_Calculated"].isin(statuses)]

# -------------------- Latest snapshot per device --------------------
def latest_per_device(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    key_cols = ["IP"] if "IP" in df.columns else [c for c in ["CID", "DeviceType"] if c in df.columns]
    if not key_cols:
        # fallback to CID only if exists
        key_cols = ["CID"] if "CID" in df.columns else df.columns[:1].tolist()
    df_sorted = df.sort_values("Timestamp").dropna(subset=["Timestamp"])
    return df_sorted.groupby(key_cols, as_index=False).tail(1)

latest = latest_per_device(df)

def safe_mean(s: pd.Series) -> float:
    try:
        return float(pd.to_numeric(s, errors="coerce").dropna().mean())
    except Exception:
        return float("nan")

total_devices = latest["IP"].nunique() if "IP" in latest.columns else latest.shape[0]
online = int((latest.get("PingStatus_Calculated") == "ONLINE").sum()) if "PingStatus_Calculated" in latest.columns else 0
offline = int((latest.get("PingStatus_Calculated") == "OFFLINE").sum()) if "PingStatus_Calculated" in latest.columns else 0
avg_loss = safe_mean(latest.get("AvgLossPercent_Summary", pd.Series(dtype=float)))

# -------------------- KPI cards --------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("TOTAL DEVICES", f"{total_devices}")
c2.metric("ONLINE", f"{online}")
c3.metric("OFFLINE", f"{offline}")
c4.metric("PACKET LOSS", f"{avg_loss:.1f}%" if pd.notna(avg_loss) else "—")

# -------------------- Charts --------------------
if not df.empty and "AvgPingMs_Summary" in df.columns:
    line_data = df.dropna(subset=["AvgPingMs_Summary"]).copy()
    line_data["DateOnly"] = line_data["Timestamp"].dt.date
    line = alt.Chart(line_data).mark_line().encode(
        x=alt.X("DateOnly:T", title="Date"),
        y=alt.Y("mean(AvgPingMs_Summary):Q", title="Avg Ping (ms)"),
        color=alt.Color("Project:N", title="Project")
    ).properties(height=260)
else:
    line = alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_line()

if not latest.empty and "PingStatus_Calculated" in latest.columns:
    pie_data = latest["PingStatus_Calculated"].value_counts().rename_axis("Status").reset_index(name="Count")
    pie = alt.Chart(pie_data).mark_arc(innerRadius=60).encode(
        theta="Count:Q", color="Status:N", tooltip=["Status", "Count"]
    ).properties(height=260)
else:
    pie = alt.Chart(pd.DataFrame({"Status": [], "Count": []})).mark_arc(innerRadius=60)

lcol, rcol = st.columns((3, 2))
with lcol:
    st.subheader("Latency Over Time")
    st.altair_chart(line, use_container_width=True)
with rcol:
    st.subheader("Device Online Status")
    st.altair_chart(pie, use_container_width=True)

# -------------------- Table + Export --------------------
st.subheader("Latest Device Snapshot")
cols = [c for c in [
    "Timestamp", "Project", "DeviceType", "CID", "IP",
    "PingStatus_Calculated", "AvgPingMs_Summary", "AvgLossPercent_Summary"
] if c in latest.columns]

if latest.empty:
    st.info("ไม่มีข้อมูลในช่วงที่เลือก / หลังกรอง")
    st.dataframe(pd.DataFrame(columns=cols), use_container_width=True)
    csv_bytes = b""
else:
    st.dataframe(latest[cols].sort_values("Project"), use_container_width=True)
    csv_bytes = latest[cols].to_csv(index=False).encode("utf-8")

st.download_button("Download filtered snapshot (CSV)", data=csv_bytes, file_name="projectping_snapshot.csv")

st.caption(f"Auto-refresh every {REFRESH_SEC}s. Data source: SHEET_CSV_URL (Google Sheet CSV).")
