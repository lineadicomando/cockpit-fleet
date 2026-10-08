import React from "react";
import { Label } from "@patternfly/react-core";
import {
    CheckCircleIcon,
    ExclamationCircleIcon,
    ExclamationTriangleIcon,
} from "@patternfly/react-icons";

export type Tone = "good" | "warning" | "bad";

const LOOK = {
    good: { color: "green", icon: <CheckCircleIcon /> },
    warning: { color: "orange", icon: <ExclamationTriangleIcon /> },
    bad: { color: "red", icon: <ExclamationCircleIcon /> },
} as const;

export function StatusLabel({ tone, children }: { tone: Tone; children: React.ReactNode }) {
    return (
        <Label color={LOOK[tone].color} icon={LOOK[tone].icon} isCompact>
            {children}
        </Label>
    );
}
