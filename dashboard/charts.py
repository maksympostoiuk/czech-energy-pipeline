"""Chart building blocks: one validated palette, recessive axes, a tooltip on every mark.

Colors are the dataviz reference palette (light mode), checked with its validator:
categorical blue / orange / aqua pass all-pairs CVD checks; blue <-> red is the
diverging pair for price polarity. Aqua is below 3:1 on the surface, so every chart
using it also carries direct labels and a table view.
"""

import altair as alt
import pandas as pd
import streamlit as st

BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
RED = "#e34948"
CATEGORICAL = [BLUE, ORANGE, AQUA]
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
MUTED = "#8b8a85"
GRID = "#e9e8e4"
PEAK_BAND = "#f0efec"


def styled(chart: alt.TopLevelMixin, height: int = 320) -> alt.TopLevelMixin:
    return (
        chart.properties(height=height, background=SURFACE)
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelColor=TEXT_SECONDARY,
            titleColor=TEXT_SECONDARY,
            titleFontWeight="normal",
            labelFontSize=12,
            titleFontSize=12,
            gridColor=GRID,
            domainColor=GRID,
            tickColor=GRID,
        )
        .configure_legend(orient="top", labelColor=TEXT_SECONDARY, titleColor=TEXT_SECONDARY)
        .configure_text(color=TEXT_SECONDARY)
    )


def show(chart: alt.TopLevelMixin, height: int = 320) -> None:
    st.altair_chart(styled(chart, height), width="stretch", theme=None)


def price_bars(df: pd.DataFrame, price: str, unit: str) -> alt.LayerChart:
    """One bar per market time unit, anchored at zero; blue above, red below zero."""
    df = df.assign(
        sign=df[price].ge(0).map({True: "≥ 0", False: "< 0"}), end_gap=df["end_h"] - 0.04
    )
    peak = df[df["is_peak"]]
    layers = []
    if not peak.empty:
        band = pd.DataFrame({"start": [peak["start_h"].min()], "end": [peak["end_h"].max()]})
        layers.append(
            alt.Chart(band).mark_rect(color=PEAK_BAND).encode(x="start:Q", x2="end:Q")
            + alt.Chart(band.assign(label="Peak 08:00–20:00"))
            .mark_text(align="left", baseline="top", dx=4, dy=4, fontSize=11)
            .encode(x="start:Q", y=alt.value(0), text="label:N")
        )
    bars = (
        alt.Chart(df)
        # rect, not bar: both axes are ranges (x..x2 interval, 0..price), so no orientation guess
        .mark_rect(cornerRadius=1)
        .encode(
            x=alt.X(
                "start_h:Q",
                title="Hours since local midnight",
                scale=alt.Scale(domain=[0, float(df["end_h"].max())], nice=False),
                axis=alt.Axis(values=list(range(0, int(df["end_h"].max()) + 1, 2)), grid=False),
            ),
            x2="end_gap:Q",
            y=alt.Y(f"{price}:Q", title=unit),
            y2=alt.datum(0),  # anchor every bar at zero, also the negative ones
            color=alt.Color(
                "sign:N",
                scale=alt.Scale(domain=["≥ 0", "< 0"], range=[BLUE, RED]),
                legend=alt.Legend(title="Price") if (df[price] < 0).any() else None,
            ),
            tooltip=[
                alt.Tooltip("local_time:N", title="Starts"),
                alt.Tooltip(f"{price}:Q", title=unit, format=",.2f"),
                alt.Tooltip("volume_mwh:Q", title="Volume MWh", format=",.1f"),
            ],
        )
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q")
    return alt.layer(*layers, bars, zero)


def labelled_lines(
    df: pd.DataFrame,
    x: str,
    x_title: str,
    y_title: str,
    series: list[tuple[str, str]],
    x_type: str = "T",
    y_format: str = ",.0f",
    x_axis: alt.Axis | None = None,
) -> alt.LayerChart:
    """Lines from wide data (one column per series) with a crosshair tooltip and a direct
    label at the end of each line. series: [(column, label), ...] in palette order."""
    labels = [label for _, label in series]
    long = df.melt(id_vars=[x], value_vars=[c for c, _ in series], var_name="column")
    long["series"] = long["column"].map(dict(series))
    color = alt.Color(
        "series:N",
        scale=alt.Scale(domain=labels, range=CATEGORICAL[: len(series)]),
        legend=alt.Legend(title=None),
    )
    x_enc = alt.X(f"{x}:{x_type}", title=x_title, axis=x_axis or alt.Axis(labelAngle=0))
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=[x], empty=False)

    lines = (
        alt.Chart(long)
        .mark_line(strokeWidth=2)
        .encode(x=x_enc, y=alt.Y("value:Q", title=y_title), color=color)
    )
    points = (
        alt.Chart(long)
        .mark_point(size=64, filled=True)
        .encode(
            x=x_enc,
            y="value:Q",
            color=color,
            opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
        )
    )
    tooltip = [alt.Tooltip(f"{x}:{x_type}", title=x_title)] + [
        alt.Tooltip(f"{column}:Q", title=label, format=y_format) for column, label in series
    ]
    hover = (
        alt.Chart(df)
        .mark_rule(color=MUTED)
        .encode(
            x=x_enc, opacity=alt.condition(nearest, alt.value(0.6), alt.value(0)), tooltip=tooltip
        )
        .add_params(nearest)
    )
    end_labels = (
        alt.Chart(_end_labels(long, x))
        .mark_text(align="left", dx=6, fontSize=12)
        .encode(x=x_enc, y="label_y:Q", text="series:N", color=alt.value(TEXT_SECONDARY))
    )
    return alt.layer(lines, points, hover, end_labels)


def _end_labels(long: pd.DataFrame, x: str, min_gap_share: float = 0.06) -> pd.DataFrame:
    """Last point of each series, with label positions pushed apart when lines end close
    together (min gap = a share of the value range), so labels never overprint."""
    last = long.dropna(subset=["value"]).sort_values(x).groupby("series", as_index=False).tail(1)
    last = last.sort_values("value").reset_index(drop=True)
    gap = (long["value"].max() - long["value"].min()) * min_gap_share
    positions: list[float] = []
    for value in last["value"]:
        positions.append(value if not positions else max(value, positions[-1] + gap))
    return last.assign(label_y=positions)


MONTH_LABELS = (
    "['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][datum.value - 1]"
)


def heatmap(df: pd.DataFrame, value: str, unit: str) -> alt.Chart:
    days = df.drop_duplicates("day_of_week").sort_values("day_of_week")["day_name"].tolist()
    return (
        alt.Chart(df)
        .mark_rect(stroke=SURFACE, strokeWidth=2, cornerRadius=2)
        .encode(
            x=alt.X("local_hour:O", title="Hour (local time)", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("day_name:N", title=None, sort=days),
            color=alt.Color(
                f"{value}:Q",
                title=unit,
                scale=alt.Scale(range=SEQUENTIAL),
                legend=alt.Legend(orient="right", gradientLength=180),
            ),
            tooltip=[
                alt.Tooltip("day_name:N", title="Day"),
                alt.Tooltip("local_hour:O", title="Hour"),
                alt.Tooltip(f"{value}:Q", title=unit, format=",.1f"),
                alt.Tooltip("hours:Q", title="Hours averaged"),
            ],
        )
    )


def single_bars(
    df: pd.DataFrame, x: str, y: str, x_title: str, y_title: str, color: str, y_format: str
) -> alt.Chart:
    return (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4, color=color)
        .encode(
            x=alt.X(f"{x}:T", title=x_title, timeUnit="yearmonth"),
            y=alt.Y(f"{y}:Q", title=y_title),
            tooltip=[
                alt.Tooltip(f"{x}:T", title=x_title, timeUnit="yearmonth"),
                alt.Tooltip(f"{y}:Q", title=y_title, format=y_format),
            ],
        )
    )


def binned_profile(df: pd.DataFrame, bin_size: float, x_title: str, unit: str) -> alt.LayerChart:
    """Mean price per weather bin with the inter-quartile range as a band."""
    df = df.assign(bin_mid=df["bin_start"] + bin_size / 2)
    base = alt.Chart(df).encode(x=alt.X("bin_mid:Q", title=x_title))
    band = base.mark_area(color=BLUE, opacity=0.18).encode(y=alt.Y("p25:Q", title=unit), y2="p75:Q")
    line = base.mark_line(color=BLUE, strokeWidth=2).encode(y="mean_price:Q")
    points = base.mark_point(color=BLUE, filled=True, size=64).encode(
        y="mean_price:Q",
        tooltip=[
            alt.Tooltip("bin_start:Q", title=f"{x_title} from", format=",.1f"),
            alt.Tooltip("mean_price:Q", title=f"Mean {unit}", format=",.1f"),
            alt.Tooltip("p25:Q", title="25th percentile", format=",.1f"),
            alt.Tooltip("p75:Q", title="75th percentile", format=",.1f"),
            alt.Tooltip("hours:Q", title="Hours", format=","),
        ],
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q")
    return alt.layer(band, line, points, zero)


def scatter_with_trend(
    df: pd.DataFrame, x: str, y: str, x_title: str, y_title: str
) -> alt.LayerChart:
    base = alt.Chart(df).encode(x=alt.X(f"{x}:Q", title=x_title), y=alt.Y(f"{y}:Q", title=y_title))
    dots = base.mark_circle(
        size=64, color=BLUE, opacity=0.45, stroke=SURFACE, strokeWidth=1
    ).encode(
        tooltip=[
            alt.Tooltip("delivery_date:T", title="Day"),
            alt.Tooltip(f"{x}:Q", title=x_title, format=",.1f"),
            alt.Tooltip(f"{y}:Q", title=y_title, format=",.1f"),
        ]
    )
    trend = base.transform_loess(x, y, bandwidth=0.4).mark_line(color=TEXT_SECONDARY, strokeWidth=2)
    return alt.layer(dots, trend)
