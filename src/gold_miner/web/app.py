"""轻量 Web 仪表盘 — Streamlit."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from gold_miner.config import settings
from gold_miner.data.accumulation_gold import AccumulationGoldFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher


st.set_page_config(page_title="AI Gold Miner", page_icon="🥇", layout="wide")

st.title("🥇 AI Gold Miner 仪表盘")

# 实时报价
st.header("实时报价")
col1, col2, col3 = st.columns(3)

with col1:
    try:
        quote = SpotGoldFetcher().fetch_realtime_quote()
        st.metric("现货黄金", f"{quote.get('domestic_price', 'N/A')}", f"{quote.get('domestic_change_pct', 0):+.2%}")
    except Exception as e:
        st.error(f"现货报价获取失败: {e}")

with col2:
    try:
        acc = AccumulationGoldFetcher().fetch_latest()
        if not acc.empty:
            st.metric("积存金 (Au99.99)", f"{acc['close'].iloc[-1]:.2f} 元/克")
        else:
            st.metric("积存金 (Au99.99)", "N/A")
    except Exception as e:
        st.error(f"积存金获取失败: {e}")

with col3:
    st.metric("风险偏好", settings.risk_profile.upper())

# 最新报告
st.header("最新报告")
reports_dir = Path("reports")
if reports_dir.exists():
    html_files = sorted(reports_dir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if html_files:
        latest = html_files[0]
        st.subheader(latest.name)
        html_content = latest.read_text(encoding="utf-8")
        st.components.v1.html(html_content, height=800, scrolling=True)
    else:
        st.info("reports/ 目录下暂无 HTML 报告，运行 `gold-miner report` 生成。")
else:
    st.info("reports/ 目录不存在。")

# CLI 提示
st.header("快速操作")
st.code("gold-miner --demo scan", language="bash")
st.code("gold-miner report", language="bash")
