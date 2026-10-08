// Turns the colour codes Ansible prints into spans the page can style.
// Only what Ansible uses is understood: reset, bold and the 16 colours.

export interface AnsiSpan {
    text: string;
    // "red", "bright-green", ... or "" for the default colour.
    color: string;
    bold: boolean;
}

const COLORS = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
// eslint-disable-next-line no-control-regex
const SGR = /\x1b\[([0-9;]*)m/g;

export function parseAnsi(input: string): AnsiSpan[] {
    const spans: AnsiSpan[] = [];
    let color = "";
    let bold = false;
    let last = 0;

    const push = (text: string) => {
        if (!text) return;
        const previous = spans[spans.length - 1];
        if (previous && previous.color === color && previous.bold === bold) previous.text += text;
        else spans.push({ text, color, bold });
    };

    for (const match of input.matchAll(SGR)) {
        push(input.slice(last, match.index));
        last = match.index + match[0].length;
        for (const part of match[1].split(";")) {
            const code = Number(part || "0");
            if (code === 0) { color = ""; bold = false; }
            else if (code === 1) bold = true;
            else if (code === 22) bold = false;
            else if (code === 39) color = "";
            else if (code >= 30 && code <= 37) color = COLORS[code - 30];
            else if (code >= 90 && code <= 97) color = `bright-${COLORS[code - 90]}`;
        }
    }
    push(input.slice(last));
    return spans;
}
