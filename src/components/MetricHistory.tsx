import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Content, Flex, FlexItem, Title, ToggleGroup, ToggleGroupItem } from "@patternfly/react-core";
import {
    Chart, ChartArea, ChartAxis, ChartGroup, ChartLine, ChartThemeColor, ChartVoronoiContainer,
} from "@patternfly/react-charts/victory";
import chart_color_blue_100 from "@patternfly/react-tokens/dist/esm/chart_color_blue_100";
import chart_color_blue_300 from "@patternfly/react-tokens/dist/esm/chart_color_blue_300";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import {
    SPANS, chartsOf, formatPointTime, formatTick, formatValue, valueDomain, valueTicks,
} from "../lib/metrics.ts";
import type { Datum, MetricChart, MetricQuery, Resolution, Span } from "../lib/metrics.ts";
import { Alert, Spinner } from "./pf.tsx";

const HEIGHT = 180;
const PADDING = { top: 10, bottom: 40, left: 70, right: 20 };

// The width of the element, followed as the page is resized: a chart is
// drawn at a size in pixels.
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
    const ref = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState(0);
    useLayoutEffect(() => {
        const element = ref.current;
        if (!element) return;
        setWidth(element.clientWidth);
        const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)));
        observer.observe(element);
        return () => observer.disconnect();
    }, []);
    return [ref, width];
}

interface Labelled {
    datum: Datum & { childName?: string };
}

function MetricGraph({ chart, span, resolution, since, until, width }: {
    chart: MetricChart; span: Span; resolution: Resolution; since: number; until: number; width: number;
}) {
    const { t, i18n } = useTranslation();
    const single = chart.lines.length === 1;
    const aggregated = resolution !== "raw";
    const name = (instance: string) => instance || chart.name;
    const label = ({ datum }: Labelled) => {
        if (datum.y === null || datum.childName === "band") return "";
        const value = formatValue(datum.y, chart.unit);
        const range = aggregated && single && datum.low !== undefined
            ? ` (${formatValue(datum.low, chart.unit)} – ${formatValue(datum.high!, chart.unit)})`
            : "";
        const who = single ? "" : `${name(datum.childName?.replace(/^line-/, "") ?? "")}: `;
        return `${formatPointTime(datum.x, resolution, i18n.language)}\n${who}${value}${range}`;
    };
    const legend = single ? undefined : chart.lines.map(line => ({ name: name(line.instance) }));
    const height = HEIGHT + (legend ? 30 : 0);
    const domain = valueDomain(chart);
    return (
        <div>
            <Title headingLevel="h4" size="md">
                <code>{chart.name}</code>
                {single && chart.lines[0].instance && <Content component="small"> · {chart.lines[0].instance}</Content>}
            </Title>
            <Chart
                ariaTitle={chart.name}
                ariaDesc={t("Values of {{metric}} over time", { metric: chart.name })}
                width={width}
                height={height}
                padding={{ ...PADDING, bottom: PADDING.bottom + (legend ? 30 : 0) }}
                scale={{ x: "time", y: "linear" }}
                domain={{ x: [since * 1000, until * 1000], y: domain }}
                themeColor={single ? ChartThemeColor.blue : ChartThemeColor.multiUnordered}
                legendData={legend}
                legendPosition="bottom-left"
                containerComponent={
                    <ChartVoronoiContainer labels={label} constrainToVisibleArea voronoiBlacklist={["band"]} />
                }
            >
                <ChartAxis crossAxis={false} tickCount={6} tickFormat={(ms: number) => formatTick(ms, span, i18n.language)} />
                <ChartAxis dependentAxis crossAxis={false} showGrid tickCount={4} tickValues={valueTicks(domain, chart.unit)} tickFormat={(value: number) => formatValue(value, chart.unit)} />
                {single && aggregated && (
                    <ChartArea
                        name="band"
                        data={chart.lines[0].data.map(datum => ({ x: datum.x, y: datum.y === null ? null : datum.high, y0: datum.low }))}
                        style={{ data: { fill: chart_color_blue_100.var, fillOpacity: 0.5, stroke: "none" } }}
                    />
                )}
                {single ? (
                    <ChartLine data={chart.lines[0].data} style={{ data: { stroke: chart_color_blue_300.var } }} />
                ) : (
                    <ChartGroup>
                        {chart.lines.map(line => <ChartLine key={line.instance} name={`line-${line.instance}`} data={line.data} />)}
                    </ChartGroup>
                )}
            </Chart>
        </div>
    );
}

// The values a check measured on a host, over a span chosen in the page.
// `version` changes when the check has new values, to load them again.
export function MetricHistory({ hostId, checkId, version }: { hostId: string; checkId: string; version: number | null }) {
    const { t } = useTranslation();
    const [span, setSpan] = useState<Span>("day");
    const [ref, width] = useWidth();
    const query = useLoad(() => {
        const until = Math.floor(Date.now() / 1000);
        return call<MetricQuery>("metric.query", { host_id: hostId, check_id: checkId, since: until - SPANS[span], until });
    });
    useEffect(query.reload, [span, version]);

    const spans: [Span, string][] = [["day", t("Day")], ["week", t("Week")], ["month", t("Month")], ["year", t("Year")]];
    const data = query.data;
    const charts = data ? chartsOf(data) : [];
    return (
        <div ref={ref} style={{ marginBlockStart: "1rem" }}>
            <Flex alignItems={{ default: "alignItemsCenter" }} gap={{ default: "gapMd" }} style={{ marginBlockEnd: "0.5rem" }}>
                <FlexItem><Title headingLevel="h4" size="md">{t("Over time")}</Title></FlexItem>
                <FlexItem>
                    <ToggleGroup isCompact aria-label={t("Span")}>
                        {spans.map(([key, label]) => (
                            <ToggleGroupItem key={key} text={label} isSelected={span === key} onChange={() => setSpan(key)} />
                        ))}
                    </ToggleGroup>
                </FlexItem>
                {query.loading && <FlexItem><Spinner size="md" aria-label={t("Loading")} /></FlexItem>}
            </Flex>
            {query.error && <Alert variant="danger" isInline isPlain title={query.error.message} />}
            {data && charts.length === 0 && <Content component="small">{t("No values in this span.")}</Content>}
            {data && width > 0 && (
                <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
                    {charts.map(chart => (
                        <FlexItem key={chart.name}>
                            <MetricGraph chart={chart} span={span} resolution={data.resolution}
                                since={data.since} until={data.until} width={width} />
                            {chart.truncated && <Content component="small">{t("Too many values: only the first ones are drawn.")}</Content>}
                        </FlexItem>
                    ))}
                </Flex>
            )}
        </div>
    );
}
