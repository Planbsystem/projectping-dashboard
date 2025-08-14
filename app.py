import os
import re
import pandas as pd
import streamlit as st
import altair as alt

# -------------------- Page setup --------------------
st.set_page_config(page_title="ProjectPing Dashboard", layout="wide")
st.title("ProjectPing Dashboard")

# Auto-refresh (no external package)
REFRESH_SEC = int(os.environ.get("REFRESH_SEC", "60"))
st.markdown(f"<meta http-equiv='refresh' content='{REFRESH_SEC}'>", unsafe_allow_html=True)

# -------------------- Helpers --------------------
def norm(s: str) -> str:
    """normalize header name for fuzzy matching"""
    s = str(s).strip().lower()
    s = re.sub(r"[\s\-\./]+", "_", s)  # spaces, -, ., / -> _
    return s

def find_col(df: pd.DataFrame, aliases) -> str | None:
    """find a column in df that matches any alias list (case-insensitive, normalized)"""
    if not len(df.columns):
        return None
    normalized = {norm(c): c for c in df.columns}
    for a in aliases:
        key = norm(a)
        if key in normalized:
            return normalized[key]
    # partial startswith match (e.g., "avg_ping_ms" vs "avg_pingms_summary")
    for a in aliases:
        key = norm(a)
        for k, orig in normalized.items():
            if k.startswith(key) or key in k:
                return orig
    return None

# alias dictionary (เพิ่ม/แก้ได้โดยไม่กระทบชีท)
ALIASES = {
    "timestamp": [
        "timestamp", "date_time", "datetime", "date-time", "วันเวลา", "เวลา_วันที่",
    ],
    "date": ["date", "วันที่"],
    "time": ["time", "เวลา"],
    "project": ["project", "site", "location", "โปรเจกต์", "โครงการ"],
    "device_type": ["device_type", "devicetype", "type", "ชนิดอุปกรณ์", "device type"],
    "cid": ["cid", "device_id", "client_id", "id"],
    "ip": ["ip", "ip_address", "address", "ipv4"],
    "status": ["pingstatus_calculated", "status", "connection_status", "state"],
    "avg_ping": ["avgpingms_summary", "avg_ping_ms", "avg_ping", "latency", "average_ping_ms"],
    "avg_loss": ["avglosspercent_summary", "avg_loss_percent", "packet_loss", "loss", "loss_percent"],
}

# -------------------- Load data --------------------
@st.cache_data(ttl=REFRESH_SEC, show_spinner=False)
def load_csv(url: str) -> pd.DataFrame:
    return pd.read_csv(url)

csv_url = os.environ.get("SHEET_CSV_URL", "").strip()
if not csv_url:
    st.warning("ยังไม่ได้ตั้ง SHEET_CSV_URL (ลิงก์ Publish-to-Web แบบ CSV ของชีท Data)")
    st.info('Manage app → Settings → Secrets:\n'
            'SHEET_CSV_URL="https://.../pub?gid=XXXX&single=true&output=csv"\n'
            'REFRESH_SEC="60"')
    st.stop()

try:
    df = load_csv(csv_url)
except Exception as e:
    st.error("โหลด CSV ไม่ได้ — ตรวจสอบว่าเป็นลิงก์ Publish to web (CSV) ของชีท Data")
    st.exception(e)
    st.stop()

# -------------------- Flexible column mapping --------------------
# map existing headers to semantic names (keep original headers for display)
col_ts   = find_col(df, ALIASES["timestamp"])
col_date = find_col(df, ALIASES["date"])
col_time = find_col(df, ALIASES["time"])

col_project   = find_col(df, ALIASES["project"])
col_type      = find_col(df, ALIASES["device_type"])
col_cid       = find_col(df, ALIASES["cid"])
col_ip        = find_col(df, ALIASES["ip"])
col_status    = find_col(df, ALIASES["status"])
col_avg_ping  = find_col(df, ALIASES["avg_ping"])
col_avg_loss  = find_col(df, ALIASES["avg_loss"])

# ---- Build a Timestamp robustly (use what's available, but never force you to rename sheet) ----
def build_timestamp(df: pd.DataFrame) -> pd.Series:
    if col_ts:
        ts = pd.to_datetime(df[col_ts], errors="coerce")
        if ts.notna().any():
            return ts

    if col_date and col_time:
        d = pd.to_datetime(df[col_date], errors="coerce")
        # try strict HH:MM:SS first, then general
        try:
            t = pd.to_datetime(df[col_time].astype(str), format="%H:%M:%S", errors="coerce").dt.time
        except Exception:
            t = pd.to_datetime(df[col_time], errors="coerce").dt.time
        return pd.to_datetime(d.astype(str) + " " + pd.Series(t, dtype="object").astype(str), errors="coerce")

    if col_date:
        return pd.to_datetime(df[col_date], errors="coerce")

    # fallback: pick first column that parses to datetime reasonably well
    best = None; score = -1
    for c in df.columns:
        parsed = pd.to_datetime(df[c], errors="coerce")
        ok = int(parsed.notna().sum())
        if ok > score and ok > 0:
            score, best = ok, parsed
    return best if best is not None else pd.Series(pd.NaT, index=df.index)

df = df.copy()
df["__Timestamp__"] = build_timestamp(df)

if df["__Timestamp__"].notna().sum() == 0:
    st.error("ไม่พบคอลัมน์เวลาที่แปลงเป็นวันเวลาได้จาก CSV — ไม่ต้องแก้ชีท แต่ช่วยแชร์ตัวอย่างหัวคอลัมน์ 3–5 อันให้ผมแม็ปเพิ่มได้ทันที")
    st.dataframe(df.head(10))
    st.stop()

# -------------------- Sidebar --------------------
with st.sidebar:
    st.header("Filters")
    date_range = st.selectbox("Date Range", ["Last 7 days", "Today", "Last 24 hours", "Custom"])
    projects = st.multiselect("Project",
                              sorted(df[col_project].dropna().unique()) if col_project else [])
    device_types = st.multiselect("Device Type",
                                  sorted(df[col_type].dropna().unique()) if col_type else [])
    statuses = st.multiselect("Connection Status",
                              ["ONLINE", "OFFLINE", "HIGH LOSS", "UNKNOWN"] if col_status else [],
                              default=["ONLINE", "OFFLINE", "HIGH LOSS", "UNKNOWN"] if col_status else [])

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
        start = pd.Timestamp(c[0]); now = pd.Timestamp(c[1]) + pd.Timedelta(days=1)
    else:
        start = now - pd.Timedelta(days=7)

mask = (df["__Timestamp__"] >= start) & (df["__Timestamp__"] <= now)
df = df.loc[mask].copy()

# -------------------- Apply other filters (only if columns exist) --------------------
if col_project and projects:
    df = df[df[col_project].isin(projects)]
if col_type and device_types:
    df = df[df[col_type].isin(device_types)]
if col_status and statuses:
    # normalize to upper when compare
    vals = set([str(x).upper() for x in statuses])
    df = df[df[col_status].astype(str).str.upper().isin(vals)]

# -------------------- Latest snapshot per device --------------------
def latest_per_device(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if col_ip:
        keys = [col_ip]
    else:
        keys = [c for c in [col_cid, col_type] if c]
        if not keys:
            keys = [df.columns[0]]  # last resort
    df_sorted = df.sort_values("__Timestamp__").dropna(subset=["__Timestamp__"])
    return df_sorted.groupby(keys, as_index=False).tail(1)

latest = latest_per_device(df)

# -------------------- KPIs --------------------
def safe_mean(s: pd.Series) -> float:
    try:
        return float(pd.to_numeric(s, errors="coerce").dropna().mean())
    except Exception:
        return float("nan")

total_devices = latest[col_ip].nunique() if col_ip and not latest.empty else latest.shape[0]
online = int((latest[col_status].astype(str).str.upper() == "ONLINE").sum()) if col_status in latest else 0
offline = int((latest[col_status].astype(str).str.upper() == "OFFLINE").sum()) if col_status in latest else 0
avg_loss = safe_mean(latest[col_avg_loss]) if col_avg_loss in latest else float("nan")

c1, c2, c3, c4 = st.columns(4)
c1.metric("TOTAL DEVICES", f"{total_devices}")
c2.metric("ONLINE", f"{online}")
c3.metric("OFFLINE", f"{offline}")
c4.metric("PACKET LOSS", f"{avg_loss:.1f}%" if pd.notna(avg_loss) else "—")

# -------------------- Charts --------------------
if not df.empty and col_avg_ping and col_project:
    chart_df = df.dropna(subset=[col_avg_ping]).copy()
    chart_df["__DateOnly__"] = pd.to_datetime(chart_df["__Timestamp__"]).dt.date
    line = alt.Chart(chart_df).mark_line().encode(
        x=alt.X("__DateOnly__:T", title="Date"),
        y=alt.Y(f"mean({col_avg_ping}):Q", title="Avg Ping (ms)"),
        color=alt.Color(f"{col_project}:N", title="Project")
    ).properties(height=260)
else:
    line = alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_line()

if not latest.empty and col_status:
    pie_data = latest[col_status].astype(str).str.upper().value_counts().rename_axis("Status").reset_index(name="Count")
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

# show only columns that actually exist
display_cols = [c for c in [
    "__Timestamp__", col_project, col_type, col_cid, col_ip,
    col_status, col_avg_ping, col_avg_loss
] if c]

if latest.empty:
    st.info("ไม่มีข้อมูลในช่วงที่เลือก / หลังกรอง")
    st.dataframe(pd.DataFrame(columns=display_cols), use_container_width=True)
    csv_bytes = b""
else:
    st.dataframe(latest[display_cols].sort_values(display_cols[1] if len(display_cols) > 1 else "__Timestamp__"),
                 use_container_width=True)
    csv_bytes = latest[display_cols].to_csv(index=False).encode("utf-8")

st.download_button("Download filtered snapshot (CSV)", data=csv_bytes, file_name="projectping_snapshot.csv")

st.caption(f"Auto-refresh every {REFRESH_SEC}s. Source: SHEET_CSV_URL (Google Sheet CSV).")
