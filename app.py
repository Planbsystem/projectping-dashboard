import os
import pandas as pd
import streamlit as st
import altair as alt

st.set_page_config(page_title="ProjectPing Dashboard", layout="wide")
st.title("ProjectPing Dashboard")

# ---- Auto-refresh (ไม่ใช้แพ็กเกจเสริม) ----
REFRESH_SEC = int(os.environ.get("REFRESH_SEC", "60"))
st.markdown(f"<meta http-equiv='refresh' content='{REFRESH_SEC}'>", unsafe_allow_html=True)

@st.cache_data(ttl=REFRESH_SEC, show_spinner=False)
def load_csv(url: str) -> pd.DataFrame:
    return pd.read_csv(url)

csv_url = os.environ.get("SHEET_CSV_URL", "").strip()
if not csv_url:
    st.warning("ยังไม่ตั้งค่า SHEET_CSV_URL (ลิงก์ Publish-to-Web แบบ CSV ของชีท Data)")
    st.info("เข้า App → Settings → Secrets/Env แล้วใส่ SHEET_CSV_URL = ลิงก์ CSV")
    st.stop()

try:
    df = load_csv(csv_url)
except Exception as e:
    st.error("โหลด CSV ไม่ได้ — ตรวจสอบลิงก์ว่าเป็น Publish to web (CSV) ของชีท Data")
    st.exception(e)
    st.stop()

# ---- สร้าง Timestamp จาก Date + Time ----
def coerce_datetime(df):
    if "Date" in df.columns and "Time" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df["Time"] = pd.to_datetime(df["Time"].astype(str), format="%H:%M:%S", errors="coerce").dt.time
        df["Timestamp"] = pd.to_datetime(df["Date"].astype(str) + " " + df["Time"].astype(str), errors="coerce")
    else:
        df["Timestamp"] = pd.to_datetime(df.iloc[:, 0], errors="coerce")
    return df

df = coerce_datetime(df)

# ---- Sidebar ----
with st.sidebar:
    st.header("Filters")
    date_range = st.selectbox("Date Range", ["Last 7 days", "Today", "Last 24 hours", "Custom"])
    projects = st.multiselect("Project", sorted(df["Project"].dropna().unique()) if "Project" in df.columns else [])
    device_types = st.multiselect("Device Type", sorted(df["DeviceType"].dropna().unique()) if "DeviceType" in df.columns else [])
    statuses = st.multiselect("Connection Status",
                              ["ONLINE","OFFLINE","HIGH LOSS","UNKNOWN"],
                              default=["ONLINE","OFFLINE","HIGH LOSS","UNKNOWN"] if "PingStatus_Calculated" in df.columns else [])

# ---- Date filter ----
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

df = df[(df["Timestamp"] >= start) & (df["Timestamp"] <= now)]

# ---- Other filters ----
if "Project" in df.columns and projects:
    df = df[df["Project"].isin(projects)]
if "DeviceType" in df.columns and device_types:
    df = df[df["DeviceType"].isin(device_types)]
if "PingStatus_Calculated" in df.columns and statuses:
    df = df[df["PingStatus_Calculated"].isin(statuses)]

# ---- Latest snapshot per device ----
def latest_per_device(df):
    key_cols = ["IP"] if "IP" in df.columns else [c for c in ["CID","DeviceType"] if c in df.columns]
    df_sorted = df.sort_values("Timestamp").dropna(subset=["Timestamp"])
    return df_sorted.groupby(key_cols, as_index=False).tail(1)

latest = latest_per_device(df) if not df.empty else df.copy()

def safe_mean(s):
    try: return float(s.dropna().astype(float).mean())
    except: return float("nan")

total_devices = latest["IP"].nunique() if "IP" in latest.columns else latest.shape[0]
online  = (latest.get("PingStatus_Calculated") == "ONLINE").sum() if "PingStatus_Calculated" in latest.columns else 0
offline = (latest.get("PingStatus_Calculated") == "OFFLINE").sum() if "PingStatus_Calculated" in latest.columns else 0
avg_loss = safe_mean(latest.get("AvgLossPercent_Summary", pd.Series(dtype=float)))

# ---- KPI ----
c1,c2,c3,c4 = st.columns(4)
c1.metric("TOTAL DEVICES", f"{total_devices}")
c2.metric("ONLINE", f"{online}")
c3.metric("OFFLINE", f"{offline}")
c4.metric("PACKET LOSS", f"{avg_loss:.1f}%" if pd.notna(avg_loss) else "—")

# ---- Charts ----
if not df.empty and "AvgPingMs_Summary" in df.columns:
    line_data = df.dropna(subset=["AvgPingMs_Summary"]).copy()
    line_data["DateOnly"] = line_data["Timestamp"].dt.date
    line = alt.Chart(line_data).mark_line().encode(
        x=alt.X("DateOnly:T", title="Date"),
        y=alt.Y("mean(AvgPingMs_Summary):Q", title="Avg Ping (ms)"),
        color=alt.Color("Project:N", title="Project")
    ).properties(height=260)
else:
    line = alt.Chart(pd.DataFrame({"x":[],"y":[]})).mark_line()

if not latest.empty and "PingStatus_Calculated" in latest.columns:
    pie_data = latest["PingStatus_Calculated"].value_counts().rename_axis("Status").reset_index(name="Count")
    pie = alt.Chart(pie_data).mark_arc(innerRadius=60).encode(
        theta="Count:Q", color="Status:N", tooltip=["Status","Count"]
    ).properties(height=260)
else:
    pie = alt.Chart(pd.DataFrame({"Status":[],"Count":[]})).mark_arc(innerRadius=60)

lcol, rcol = st.columns((3,2))
with lcol:
    st.subheader("Latency Over Time")
    st.altair_chart(line, use_container_width=True)
with rcol:
    st.subheader("Device Online Status")
    st.altair_chart(pie, use_container_width=True)

st.subheader("Latest Device Snapshot")
cols = [c for c in ["Timestamp","Project","DeviceType","CID","IP",
                    "PingStatus_Calculated","AvgPingMs_Summary","AvgLossPercent_Summary"]
        if c in latest.columns]
st.dataframe(latest[cols].sort_values("Project") if not latest.empty else pd.DataFrame(columns=cols),
             use_container_width=True)

csv_bytes = latest[cols].to_csv(index=False).encode("utf-8") if not latest.empty else b""
st.download_button("Download filtered snapshot (CSV)", data=csv_bytes, file_name="projectping_snapshot.csv")

st.caption(f"Auto-refresh every {REFRESH_SEC}s. Set SHEET_CSV_URL (Google Sheet CSV) in app settings.")
