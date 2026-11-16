import React from "react";
import { useTranslation } from "react-i18next";
import {
    Chart, ChartAxis, ChartBar, ChartDonut, ChartGroup, ChartLabel, ChartStack, ChartTooltip,
} from "@patternfly/react-charts/victory";
import chart_color_black_200 from "@patternfly/react-tokens/dist/esm/chart_color_black_200";
import chart_color_black_300 from "@patternfly/react-tokens/dist/esm/chart_color_black_300";
import chart_color_purple_300 from "@patternfly/react-tokens/dist/esm/chart_color_purple_300";
import t_global_color_status_danger_default from "@patternfly/react-tokens/dist/esm/t_global_color_status_danger_default";
import t_global_color_status_success_default from "@patternfly/react-tokens/dist/esm/t_global_color_status_success_default";
import t_global_color_status_warning_default from "@patternfly/react-tokens/dist/esm/t_global_color_status_warning_default";
import { useWidth } from "../lib/hooks.ts";
import { useStateNames } from "./ChecksPage.tsx";
import { AXIS, LEGEND, SUBTLE, TEXT } from "./chartText.tsx";
import { countTicks, dayLabel, dayTicks, stateSlices, totalOf } from "../lib/overview.ts";
import type { AlertDay, CountName, RunDay } from "../lib/overview.ts";
import type { StateCounts } from "../lib/types.ts";

// The colours of the states are those of their labels elsewhere in the page.
const STATE_COLOR: Record<CountName, string> = {
    ok: t_global_color_status_success_default.var,
    warning: t_global_color_status_warning_default.var,
    critical: t_global_color_status_danger_default.var,
    unknown: chart_color_purple_300.var,
    pending: chart_color_black_200.var,
};

const GOOD = t_global_color_status_success_default.var;
const BAD = t_global_color_status_danger_default.var;
const STOPPED = chart_color_black_300.var;
const HEIGHT = 200;
const PADDING = { top: 10, bottom: 70, left: 45, right: 15 };

// The checks on the hosts by state, as a ring with the total inside.
export function StatesDonut({ states }: { states: StateCounts }) {
    const { t } = useTranslation();
    const names = useStateNames();
    const slices = stateSlices(states);
    const total = totalOf(states);
    const legend = slices.map(slice => ({ name: `${names[slice.state]}: ${slice.count}` }));
    return (
        <div style={{ maxWidth: "22rem" }}>
            <ChartDonut
                ariaTitle={t("Checks by state")}
                ariaDesc={slices.map(slice => `${names[slice.state]} ${slice.count}`).join(", ")}
                constrainToVisibleArea
                data={slices.map(slice => ({ x: names[slice.state], y: slice.count }))}
                colorScale={slices.map(slice => STATE_COLOR[slice.state])}
                labels={({ datum }: { datum: { x: string; y: number } }) => `${datum.x}: ${datum.y}`}
                legendData={legend}
                legendOrientation="vertical"
                legendPosition="right"
                legendAllowWrap
                legendComponent={LEGEND}
                titleComponent={<ChartLabel style={[{ fill: TEXT, fontSize: 24 }, { fill: SUBTLE, fontSize: 14 }]} />}
                padding={{ top: 10, bottom: 10, left: 10, right: 150 }}
                width={330}
                height={170}
                title={String(total)}
                subTitle={t("checks_on_hosts", { count: total })}
                style={{ data: { stroke: "var(--pf-t--global--background--color--primary--default)", strokeWidth: 2 } }}
            />
        </div>
    );
}

interface Series<T> {
    key: keyof T & string;
    name: string;
    color: string;
}

function barWidth(width: number, days: number, perDay: number): number {
    const room = (width - PADDING.left - PADDING.right) / days;
    return Math.max(2, Math.min(22, Math.floor((room * 0.7) / perDay)));
}

// Bars of a window of days, stacked or side by side, on a count axis.
function DayBars<T extends { start: number }>({ days, series, stacked, ariaTitle }: {
    days: T[]; series: Series<T>[]; stacked: boolean; ariaTitle: string;
}) {
    const { i18n } = useTranslation();
    const [ref, width] = useWidth();
    const labels = days.map(day => dayLabel(day.start, i18n.language));
    const highest = Math.max(1, ...days.map(day => stacked
        ? series.reduce((sum, item) => sum + Number(day[item.key]), 0)
        : Math.max(...series.map(item => Number(day[item.key])))));
    const ticks = dayTicks(days.map((_day, index) => index));
    const counts = countTicks(highest);
    const size = barWidth(width, days.length, stacked ? 1 : series.length);
    const bars = series.map(item => (
        <ChartBar
            key={item.key}
            name={item.key}
            data={days.map((day, index) => ({ x: index, y: Number(day[item.key]), name: item.name, day: labels[index] }))}
            barWidth={size}
            cornerRadius={stacked ? undefined : { top: 2 }}
            style={{ data: { fill: item.color, stroke: "var(--pf-t--global--background--color--primary--default)", strokeWidth: stacked ? 1 : 0 } }}
            labels={({ datum }: { datum: { y: number; name: string; day: string } }) => datum.y ? `${datum.day}\n${datum.name}: ${datum.y}` : ""}
            labelComponent={<ChartTooltip constrainToVisibleArea />}
        />
    ));
    return (
        <div ref={ref}>
            {width > 0 && (
                <Chart
                    ariaTitle={ariaTitle}
                    width={width}
                    height={HEIGHT}
                    padding={PADDING}
                    domain={{ x: [-0.5, days.length - 0.5], y: [0, counts[counts.length - 1]] }}
                    domainPadding={{ x: 0, y: [0, 6] }}
                    legendData={series.map(item => ({ name: item.name, symbol: { fill: item.color } }))}
                    legendPosition="bottom-left"
                    legendComponent={LEGEND}
                >
                    <ChartAxis style={AXIS} tickValues={ticks} tickFormat={(index: number) => labels[index] ?? ""} />
                    <ChartAxis style={AXIS} dependentAxis showGrid tickValues={counts} tickFormat={(value: number) => String(value)} />
                    {stacked ? <ChartStack>{bars}</ChartStack> : <ChartGroup offset={size + 2}>{bars}</ChartGroup>}
                </Chart>
            )}
        </div>
    );
}

// The runs of each day, stacked by outcome.
export function RunsChart({ days }: { days: RunDay[] }) {
    const { t } = useTranslation();
    return (
        <DayBars days={days} stacked ariaTitle={t("Runs by day and outcome")} series={[
            { key: "successful", name: t("Successes"), color: GOOD },
            { key: "failed", name: t("Failures"), color: BAD },
            { key: "stopped", name: t("Stopped"), color: STOPPED },
        ]} />
    );
}

// The problems that began and the recoveries of each day, side by side.
export function AlertsChart({ days }: { days: AlertDay[] }) {
    const { t } = useTranslation();
    return (
        <DayBars days={days} stacked={false} ariaTitle={t("Problems and recoveries by day")} series={[
            { key: "problems", name: t("Problems"), color: BAD },
            { key: "recoveries", name: t("Recoveries"), color: GOOD },
        ]} />
    );
}
