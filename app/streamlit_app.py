"""Rift Decisions — Week 8 playable interaction (Member D).

Run from the repository root:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import html
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # streamlit puts only app/ on sys.path
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import session as game  # noqa: E402
from app.map_view import BLUE, RED, build_map, build_probability_history  # noqa: E402

STAT_LABELS = {
    "total_gold": "Gold",
    "kills": "Kills",
    "towers_destroyed": "Towers destroyed",
    "dragons_secured": "Dragons",
    "barons_secured": "Barons",
}
STAT_NOUNS = {"total_gold": "gold", "kills": "kill", "towers_destroyed": "tower",
              "dragons_secured": "dragon", "barons_secured": "Baron"}

st.set_page_config(page_title="Rift Decisions", page_icon="🗺️", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.6rem;}
.prob-bar {display:flex; height:34px; border-radius:6px; overflow:hidden; gap:2px;
           font-weight:600; color:#fff; font-size:15px;}
.prob-bar div {display:flex; align-items:center; padding:0 12px;}
.stat-up {font-weight:700;}
.effect {color: var(--text-color); opacity:.8; font-size:0.88rem; margin:-4px 0 10px 2px; line-height:1.45;}
</style>
""", unsafe_allow_html=True)


def effects_text(change: dict) -> str:
    parts = []
    for side in ("blue", "red"):
        for stat, amount in change.get(side, {}).items():
            noun = STAT_NOUNS.get(stat, stat)
            plural = "" if abs(amount) == 1 or noun == "gold" else "s"
            parts.append(f"{side.capitalize()} {amount:+,} {noun}{plural}")
    effects = ", ".join(parts) if parts else "no objective change"
    return f"{effects} · {change['seconds']}s"


def prob_bar(p_blue: float) -> str:
    b = max(min(p_blue * 100, 97.0), 3.0)
    return (f'<div class="prob-bar"><div style="width:{b:.1f}%;background:{BLUE}">Blue {p_blue:.1%}</div>'
            f'<div style="width:{100 - b:.1f}%;background:{RED};justify-content:flex-end">'
            f'Red {1 - p_blue:.1%}</div></div>')


def start_session(seed: int) -> None:
    st.session_state.game = game.new_session(seed, game.MODEL_PATH)
    st.session_state.error = None


# ---------- sidebar: reproducibility controls and provenance ----------
default_seed = int(os.getenv("RANDOM_SEED", "42"))
with st.sidebar:
    st.header("Session")
    seed = st.number_input("Random seed", min_value=0, max_value=2**31 - 1, value=default_seed, step=1,
                           help="Same seed + same action = same outcome. Logged with every decision.")
    c1, c2 = st.columns(2)
    if c1.button("Restart", width="stretch"):
        start_session(int(seed))
    if c2.button("Random seed", width="stretch"):
        start_session(random.randrange(2**31))

if "game" not in st.session_state:
    start_session(int(seed))
g = st.session_state.game

try:
    info = game.model_info(Path(g["model_path"]))
except (OSError, ValueError, KeyError) as exc:
    st.error(f"Could not load the published model at {g['model_path']}: {type(exc).__name__}. "
             "Run B's training (python -m src.modeling.train) or the Airflow DAG first.")
    st.stop()

with st.sidebar:
    st.caption(f"Session `{g['session_id'][:8]}` · seed **{g['seed']}**")
    st.divider()
    st.subheader("Model")
    test = info["metrics"].get("test", {})
    st.markdown(f"Version `{info['model_version']}` (format {info['model_format_version']})")
    if test:
        st.markdown(
            f"Held-out test Brier **{test['logistic']['brier']:.3f}** vs baseline "
            f"{test['baseline']['brier']:.3f}  \nLog loss **{test['logistic']['log_loss']:.3f}** "
            f"vs {test['baseline']['log_loss']:.3f}")
    st.caption("Inputs: " + ", ".join(info["features"]))
    st.divider()
    log_path = game.decision_log_path()
    shown = log_path.relative_to(ROOT) if log_path.is_relative_to(ROOT) else log_path
    st.caption(f"Decisions are appended to `{shown}`.")

# ---------- header + probability ----------
scenario = g["scenario"]
state = g["state"]
last = g["decisions"][-1] if g["decisions"] else None
p_now = g["history"][-1]["p_blue"]

st.title("Rift Decisions")
st.caption("Action success chances are simulation assumptions. Win probability comes from the model "
           "trained on historical ranked matches. Post-match exploration only, not a live feed.")

h1, h2 = st.columns([3, 1])
with h1:
    st.markdown(f"**Blue win probability at {game.format_clock(state['elapsed_seconds'])}**")
    st.markdown(prob_bar(p_now), unsafe_allow_html=True)
with h2:
    delta = None
    if last:
        delta = f"{100 * (last['blue_win_after'] - last['blue_win_before']):+.1f} pts"
    st.metric("Blue", f"{p_now:.1%}", delta=delta)

# ---------- map + event panel ----------
left, right = st.columns([3, 2], gap="large")
with left:
    st.plotly_chart(build_map(state, last_decision=last, pending_event=last is None),
                    width="stretch", config={"displayModeBar": False})

with right:
    st.subheader(f"⏱ {game.format_clock(state['elapsed_seconds'])} · "
                 + ("Event resolved" if last else "Event"))
    st.markdown(scenario["event"])
    actions = game.available_actions(g)
    if actions:
        st.markdown("**Choose Blue's play:**")
        for action_id, action in actions.items():
            label = f"{action['label']}  ·  {action['success_chance']:.0%} success"
            if st.button(label, key=f"act_{action_id}", type="primary", width="stretch"):
                try:
                    game.take_action(g, action_id)
                except Exception as exc:  # surface engine/model errors without crashing the page
                    st.session_state.error = f"{type(exc).__name__}: {exc}"
                st.rerun()
            st.markdown(
                f'<div class="effect">✓ {html.escape(effects_text(action["success"]))}<br>'
                f'✗ {html.escape(effects_text(action["failure"]))}</div>', unsafe_allow_html=True)
    if st.session_state.get("error"):
        st.error(st.session_state.error)

    if last:
        ok = last["outcome"] == "success"
        box = st.success if ok else st.error
        box(f"**{last['action_label']}: {'success' if ok else 'failure'}**  \n"
            f"Draw {last['random_draw']:.3f} vs success chance {last['success_chance']:.2f} "
            f"(succeeds when draw < chance) · seed {last['random_seed']}  \n"
            f"{last['seconds_spent']}s elapsed → {game.format_clock(last['after_state']['elapsed_seconds'])}")
        st.markdown(
            f"Blue win probability **{last['blue_win_before']:.1%} → {last['blue_win_after']:.1%}** "
            f"({100 * (last['blue_win_after'] - last['blue_win_before']):+.1f} pts)")
        st.info("Week 8 has one decision per event. Press **Restart** (same seed) or "
                "**Random seed** in the sidebar to play it again.")

# ---------- team stats + history ----------
s1, s2 = st.columns([3, 2], gap="large")
with s1:
    st.subheader("Team statistics")
    before = last["before_state"] if last else None
    rows = []
    for stat, label in STAT_LABELS.items():
        cells = []
        for side in ("blue", "red"):
            value = state[side][stat]
            change = value - before[side][stat] if before else 0
            text = f"{value:,}"
            if change:
                text = f'<span class="stat-up">{text} ({change:+,})</span>'
            cells.append(text)
        diff = state["blue"][stat] - state["red"][stat]
        rows.append(f"<tr><td>{label}</td><td>{cells[0]}</td><td>{cells[1]}</td><td>{diff:+,}</td></tr>")
    st.markdown(
        f'<table style="width:100%"><thead><tr><th></th>'
        f'<th><span style="color:{BLUE}">■</span> Blue</th>'
        f'<th><span style="color:{RED}">■</span> Red</th><th>Blue − Red</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>', unsafe_allow_html=True)
    st.caption("Changes from the last decision are shown in brackets.")
with s2:
    st.subheader("Blue win probability by decision")
    st.plotly_chart(build_probability_history(g["history"]), width="stretch",
                    config={"displayModeBar": False})

if g["decisions"]:
    with st.expander("Decision log (this session)"):
        st.json(g["decisions"], expanded=False)
