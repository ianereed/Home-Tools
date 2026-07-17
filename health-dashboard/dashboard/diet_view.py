"""Diet page — DASH/Mediterranean/Blue Zones/Portfolio research, the
diet-tracking-app recommendation, and (once populated) nutrition trends.

Unlike the Cardiology page this page carries no PHI and is never gated —
app.py wires it in unconditionally. Content lives in diet_content.py so this
module stays pure rendering.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import diet_content as content
from dashboard import lib

_DIET_CSS = """
<style>
.diet-reco{background:#10261a;border-left:3px solid #4ade80;padding:14px 18px;
  border-radius:6px;margin:14px 0;}
.diet-reco h4{margin:0 0 6px;color:#e6e9ef;}
.diet-reco .tagline{color:#8b93a7;font-size:12px;margin-bottom:8px;}
.diet-reco .caveat{color:#8b93a7;font-size:12px;margin-top:8px;font-style:italic;}
</style>
"""


def _sources_md(sources: list[tuple[str, str]]) -> str:
    return "\n".join(f"- [{label}]({url})" for label, url in sources)


def _render_sections():
    st.markdown("## Diet research")
    for i, sec in enumerate(content.SECTIONS):
        with st.expander(sec["title"], expanded=(i == 0)):
            st.markdown(sec["body_md"])
            for point in sec["key_points"]:
                st.markdown(f"- {point}")
            st.caption("Sources")
            st.markdown(_sources_md(sec["sources"]))


def _render_recommendation():
    reco = content.RECOMMENDATION
    why_html = "".join(f"<li>{w}</li>" for w in reco["why"])
    st.markdown(
        f'<div class="diet-reco"><h4>{reco["app"]}</h4>'
        f'<div class="tagline">{reco["tagline"]}</div>'
        f'<ul>{why_html}</ul>'
        f'<div class="caveat">{reco["caveat"]}</div></div>',
        unsafe_allow_html=True)
    with st.expander("End-to-end integration path"):
        for i, step in enumerate(reco["integration_path"], 1):
            st.markdown(f"{i}. {step}")
        st.caption("Sources")
        st.markdown(_sources_md(reco["sources"]))


def _render_nutrition(load_df, days):
    st.markdown("## Nutrition data")
    df = load_df(
        "SELECT date, calories_kcal, protein_g, carbs_g, fat_g, saturated_fat_g, "
        "fiber_g, sugar_g, sodium_mg, potassium_mg, source FROM nutrition_daily "
        "WHERE date >= date('now', ?) ORDER BY date", (f"-{days} days",))
    if df.empty:
        st.caption("No nutrition data yet — see the recommendation above for the "
                   "planned integration.")
        return

    df["date"] = pd.to_datetime(df["date"])
    targets = content.DASH_TARGETS

    def _bar(y_col, title, height, hlines):
        d = df[df[y_col].notna()]
        if d.empty:
            return
        st.markdown(f"### {title}")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=d["date"], y=d[y_col], marker_color=lib.ACCENT, opacity=0.7))
        for y, label, color in hlines:
            fig.add_hline(y=y, line_dash="dot", line_color=color, annotation_text=label,
                          annotation_position="top left")
        st.plotly_chart(lib.apply_theme(fig, height), use_container_width=True,
                        key=f"diet_{y_col}")

    _bar("sodium_mg", "Sodium (mg)", 200, [
        (targets["sodium_mg_ceiling"], f'ceiling {targets["sodium_mg_ceiling"]}', lib.WARN),
        (targets["sodium_mg_ideal"], f'DASH ideal {targets["sodium_mg_ideal"]}', lib.GOOD),
    ])

    satfat = df[df["saturated_fat_g"].notna() & df["calories_kcal"].notna()
                & (df["calories_kcal"] > 0)].copy()
    if not satfat.empty:
        satfat["sat_fat_pct"] = satfat["saturated_fat_g"] * 9 / satfat["calories_kcal"] * 100
        st.markdown("### Saturated fat (% of calories)")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=satfat["date"], y=satfat["sat_fat_pct"],
                             marker_color=lib.ACCENT, opacity=0.7))
        fig.add_hline(y=targets["saturated_fat_pct_calories"], line_dash="dot",
                      line_color=lib.WARN,
                      annotation_text=f'DASH target ≤{targets["saturated_fat_pct_calories"]}%',
                      annotation_position="top left")
        st.plotly_chart(lib.apply_theme(fig, 200), use_container_width=True, key="diet_satfat_pct")

    _bar("fiber_g", "Fiber (g)", 200, [
        (targets["fiber_g_general_target"],
         f'general target {targets["fiber_g_general_target"]}g', lib.GOOD),
    ])
    _bar("potassium_mg", "Potassium (mg)", 200, [
        (targets["potassium_mg"], f'DASH target {targets["potassium_mg"]}', lib.GOOD),
    ])


def render_diet(load_df, days):
    st.markdown(_DIET_CSS, unsafe_allow_html=True)
    st.markdown("# Diet")
    st.markdown(content.STARTING_APPROACH)
    st.caption(content.ACTIVITY_GUIDELINES)

    _render_sections()
    st.markdown("## Recommended tracking app")
    _render_recommendation()
    _render_nutrition(load_df, days)
