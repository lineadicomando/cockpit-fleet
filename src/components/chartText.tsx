import React from "react";
import { ChartLegend } from "@patternfly/react-charts/victory";

// The theme of the charts draws its text in a grey that the dark theme of
// the page hides: the text of the page instead.
export const TEXT = "var(--pf-t--global--text--color--regular)";
export const SUBTLE = "var(--pf-t--global--text--color--subtle)";
export const LEGEND = <ChartLegend style={{ labels: { fill: TEXT, fontSize: 14 } }} />;
export const AXIS = { tickLabels: { fill: SUBTLE, fontSize: 12 } };
