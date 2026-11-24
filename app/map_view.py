"""Stylized Summoner's Rift drawn with Plotly shapes (no Riot image assets).

Coordinates follow Riot's timeline convention: x/y in map units, origin at the
bottom-left (blue base), red base at the top-right, roughly 0-15000 on each axis.
Tower and pit positions are approximate and for display only; they are scenario
metadata, never model inputs (docs/data_contract.md).

The game state only stores tower *counts*, so which towers appear destroyed is
a display convention (FALL_ORDER) chosen to match the scenario narrative: blue
starts with one tower (red bot outer) and the dragon event's push targets red
mid outer.
"""
from __future__ import annotations

import plotly.graph_objects as go

BLUE = "#2a6fdb"
RED = "#d9453b"
NEUTRAL = "#9aa39a"
MAP_SIZE = 15000

GROUND = "#1f3a2b"
JUNGLE = "#2a4a36"
LANE = "#8c7b5c"
RIVER = "#3d6f8f"
INK = "#f0efec"
MUTED = "#c3c2b7"

# (key, label, x, y)
TOWERS = {
    "blue": [
        ("top_outer", "Top outer", 981, 10441), ("top_inner", "Top inner", 1512, 6699),
        ("top_inhib", "Top inhibitor", 1169, 4287),
        ("mid_outer", "Mid outer", 5846, 6396), ("mid_inner", "Mid inner", 5048, 4812),
        ("mid_inhib", "Mid inhibitor", 3651, 3696),
        ("bot_outer", "Bot outer", 10504, 1029), ("bot_inner", "Bot inner", 6919, 1483),
        ("bot_inhib", "Bot inhibitor", 4281, 1253),
        ("nexus_top", "Nexus (top)", 1748, 2270), ("nexus_bot", "Nexus (bot)", 2177, 1807),
    ],
    "red": [
        ("top_outer", "Top outer", 4318, 13875), ("top_inner", "Top inner", 7943, 13411),
        ("top_inhib", "Top inhibitor", 10481, 13650),
        ("mid_outer", "Mid outer", 8955, 8510), ("mid_inner", "Mid inner", 9767, 10113),
        ("mid_inhib", "Mid inhibitor", 11134, 11207),
        ("bot_outer", "Bot outer", 13866, 4505), ("bot_inner", "Bot inner", 13327, 8226),
        ("bot_inhib", "Bot inhibitor", 13624, 10572),
        ("nexus_top", "Nexus (top)", 12611, 13084), ("nexus_bot", "Nexus (bot)", 13052, 12612),
    ],
}
# Order in which a side's towers are shown as destroyed, per count lost.
FALL_ORDER = {
    "red": ["bot_outer", "mid_outer", "top_outer", "bot_inner", "mid_inner", "top_inner",
            "bot_inhib", "mid_inhib", "top_inhib", "nexus_bot", "nexus_top"],
    "blue": ["bot_outer", "top_outer", "mid_outer", "bot_inner", "top_inner", "mid_inner",
             "bot_inhib", "top_inhib", "mid_inhib", "nexus_bot", "nexus_top"],
}
NEXUS = {"blue": (1550, 1650), "red": (13300, 13300)}
DRAGON_PIT = (9866, 4414)
BARON_PIT = (5007, 10471)
BLUE_JUNGLE = (7400, 3300)  # where blue's jungler starts the event (display only)

# Where each action of the dragon scenario points on the map.
ACTION_TARGETS = {
    "contest_dragon": DRAGON_PIT,
    "push_mid_tower": (8955, 8510),
}


def destroyed_towers(state: dict) -> dict[str, set[str]]:
    """Map tower counts to tower keys. A side loses towers the *other* side destroyed."""
    lost = {"red": state["blue"]["towers_destroyed"], "blue": state["red"]["towers_destroyed"]}
    return {side: set(FALL_ORDER[side][:min(n, len(FALL_ORDER[side]))]) for side, n in lost.items()}


def _lane_shapes():
    w = 30  # map units are large, so widths are in pixels
    lanes = [
        # top lane: up the left edge then across the top
        dict(type="path", path="M 1200,2600 L 1200,13650 L 12400,13650"),
        # bot lane: along the bottom then up the right edge
        dict(type="path", path="M 2600,1200 L 13650,1200 L 13650,12400"),
        # mid lane
        dict(type="line", x0=2300, y0=2300, x1=12700, y1=12700),
    ]
    for lane in lanes:
        lane.update(line=dict(color=LANE, width=w * 0.45), layer="below")
    river = dict(type="path", path="M 1200,13700 Q 7400,7600 13700,1200",
                 line=dict(color=RIVER, width=22), layer="below")
    jungles = [
        dict(type="circle", x0=x - r, y0=y - r, x1=x + r, y1=y + r,
             fillcolor=JUNGLE, line=dict(width=0), layer="below")
        for x, y, r in [(4000, 7600, 1900), (7400, 3800, 1900), (11000, 7400, 1900), (7600, 11200, 1900)]
    ]
    bases = [
        dict(type="path", path="M 0,0 L 0,4600 Q 4600,4600 4600,0 Z",
             fillcolor="rgba(42,111,219,0.22)", line=dict(width=0), layer="below"),
        dict(type="path", path=f"M {MAP_SIZE},{MAP_SIZE} L {MAP_SIZE},10400 Q 10400,10400 10400,{MAP_SIZE} Z",
             fillcolor="rgba(217,69,59,0.22)", line=dict(width=0), layer="below"),
    ]
    return jungles + bases + [river] + lanes


def _objective_color(taker: str | None) -> str:
    return {"blue": BLUE, "red": RED}.get(taker, NEUTRAL)


def build_map(state: dict, last_decision: dict | None = None, pending_event: bool = True,
              height: int = 560) -> go.Figure:
    """Return the map for ``state``.

    pending_event highlights the dragon pit and red mid tower before a decision.
    last_decision (a session decision record) draws the chosen action and outcome.
    """
    fig = go.Figure()
    down = destroyed_towers(state)

    for side, color in (("blue", BLUE), ("red", RED)):
        alive = [t for t in TOWERS[side] if t[0] not in down[side]]
        dead = [t for t in TOWERS[side] if t[0] in down[side]]
        name = side.capitalize()
        fig.add_trace(go.Scatter(
            x=[t[2] for t in alive], y=[t[3] for t in alive], mode="markers",
            marker=dict(symbol="square", size=13, color=color, line=dict(color=GROUND, width=2)),
            name=f"{name} tower", legendgroup=side,
            hovertemplate=[f"{name} {t[1]} tower<br>standing<extra></extra>" for t in alive]))
        fig.add_trace(go.Scatter(
            x=[t[2] for t in dead], y=[t[3] for t in dead], mode="markers",
            marker=dict(symbol="square-x-open", size=13, color=MUTED, line=dict(width=2)),
            name=f"{name} tower (destroyed)", legendgroup=side, showlegend=bool(dead),
            hovertemplate=[f"{name} {t[1]} tower<br>destroyed<extra></extra>" for t in dead]))
        nx, ny = NEXUS[side]
        fig.add_trace(go.Scatter(
            x=[nx], y=[ny], mode="markers", showlegend=False,
            marker=dict(symbol="diamond", size=22, color=color, line=dict(color=INK, width=2)),
            hovertemplate=f"{name} nexus<extra></extra>"))

    # Objective pits: ring colour = team that took the latest one in this session.
    dragon_taker = None
    if last_decision:
        b, a = last_decision["before_state"], last_decision["after_state"]
        for side in ("blue", "red"):
            if a[side]["dragons_secured"] > b[side]["dragons_secured"]:
                dragon_taker = side
    for (x, y), label, taker, symbol in (
        (DRAGON_PIT, "Dragon", dragon_taker, "circle"),
        (BARON_PIT, "Baron", None, "circle"),
    ):
        detail = (f"taken by {taker.capitalize()}" if taker else
                  ("alive: event objective" if label == "Dragon" and pending_event else "alive"))
        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode="markers+text", showlegend=False,
            text=[label], textposition="bottom center", textfont=dict(color=INK, size=12),
            marker=dict(symbol=symbol, size=26, color="rgba(0,0,0,0)",
                        line=dict(color=_objective_color(taker), width=4)),
            hovertemplate=f"{label} pit<br>{detail}<extra></extra>"))

    annotations = []
    if pending_event:
        for (x, y), text in ((DRAGON_PIT, "Dragon up"), (ACTION_TARGETS["push_mid_tower"], "Damaged")):
            annotations.append(dict(x=x, y=y, text=text, showarrow=True, arrowhead=0, ax=40, ay=-34,
                                    font=dict(color=INK, size=12), arrowcolor=MUTED,
                                    bgcolor="rgba(20,20,20,0.65)", borderpad=3))
    if last_decision and last_decision["action_id"] in ACTION_TARGETS:
        tx, ty = ACTION_TARGETS[last_decision["action_id"]]
        ok = last_decision["outcome"] == "success"
        annotations.append(dict(
            x=tx, y=ty, ax=BLUE_JUNGLE[0], ay=BLUE_JUNGLE[1], axref="x", ayref="y",
            showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=3, arrowcolor=BLUE, text=""))
        annotations.append(dict(
            x=tx, y=ty, text=f"{'✓ Success' if ok else '✗ Failed'}: {last_decision['action_label']}",
            showarrow=False, yshift=34, font=dict(color=INK, size=13),
            bgcolor="rgba(20,20,20,0.75)", bordercolor=BLUE if ok else RED, borderwidth=2, borderpad=4))

    fig.update_layout(
        shapes=_lane_shapes(), annotations=annotations, height=height,
        margin=dict(l=0, r=0, t=0, b=34), plot_bgcolor=GROUND, paper_bgcolor=GROUND,
        xaxis=dict(range=[0, MAP_SIZE], visible=False, fixedrange=True),
        yaxis=dict(range=[0, MAP_SIZE], visible=False, fixedrange=True, scaleanchor="x"),
        legend=dict(orientation="h", y=0, x=0.5, xanchor="center", yanchor="top",
                    font=dict(color=INK, size=11), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#1a1a19", font=dict(color=INK)),
    )
    return fig


def build_probability_history(history: list[dict], height: int = 220) -> go.Figure:
    """Blue win probability by decision. One series, so no legend box."""
    x = [h["label"] for h in history]
    y = [100 * h["p_blue"] for h in history]
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines+markers+text", line=dict(color=BLUE, width=2),
        marker=dict(size=9, color=BLUE, line=dict(color="#fcfcfb", width=2)),
        text=[f"{v:.1f}%" for v in y], textposition="top center",
        customdata=[f"{int(h['elapsed_seconds']) // 60}:{int(h['elapsed_seconds']) % 60:02d}" for h in history],
        hovertemplate="%{x} · %{customdata}<br>Blue win %{y:.1f}%<extra></extra>"))
    fig.add_hline(y=50, line=dict(color="#b5b4ae", width=1, dash="dot"))
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(range=[0, 100], ticksuffix="%", gridcolor="rgba(128,128,128,0.18)", zeroline=False),
        xaxis=dict(showgrid=False),
    )
    return fig
