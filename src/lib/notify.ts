// The settings of a notification channel as the form edits them: every field
// a string, whatever the kind, so that changing kind loses nothing typed.

import type { Channel, ChannelKind } from "./types.ts";

export interface ChannelForm {
    chat_id: string;
    server: string;
    url: string;
    method: string;
    template: string;
    header: string;
    host: string;
    port: string;
    security: string;
    username: string;
    sender: string;
    recipients: string;
    homeserver: string;
    room_id: string;
}

export const TELEGRAM_SERVER = "https://api.telegram.org";
export const DEFAULT_TEMPLATE = '{"text": "$text"}';

export const EMPTY_FORM: ChannelForm = {
    chat_id: "", server: TELEGRAM_SERVER, url: "", method: "POST", template: DEFAULT_TEMPLATE,
    header: "Authorization", host: "", port: "", security: "starttls", username: "", sender: "",
    recipients: "", homeserver: "", room_id: "",
};

// Addresses one per line, or separated by commas or spaces.
export function splitRecipients(text: string): string[] {
    return text.split(/[\s,;]+/).map(part => part.trim()).filter(Boolean);
}

// What channel.create and channel.update take for a kind. The backend checks it.
export function settingsOf(kind: ChannelKind, form: ChannelForm): Record<string, unknown> {
    switch (kind) {
    case "telegram":
        return { chat_id: form.chat_id.trim(), server: form.server.trim() || TELEGRAM_SERVER };
    case "webhook":
        return { url: form.url.trim(), method: form.method, template: form.template.trim() || DEFAULT_TEMPLATE, header: form.header.trim() || "Authorization" };
    case "smtp": {
        const settings: Record<string, unknown> = {
            host: form.host.trim(), security: form.security, username: form.username.trim(),
            sender: form.sender.trim(), recipients: splitRecipients(form.recipients),
        };
        // Empty: the port of the kind of security.
        if (form.port.trim()) settings.port = Number(form.port.trim());
        return settings;
    }
    case "matrix":
        return { homeserver: form.homeserver.trim(), room_id: form.room_id.trim() };
    }
}

export function formOf(channel: Channel): ChannelForm {
    const form: ChannelForm = { ...EMPTY_FORM };
    for (const [key, value] of Object.entries(channel.settings)) {
        if (!(key in form)) continue;
        (form as unknown as Record<string, string>)[key] = Array.isArray(value) ? value.join("\n") : String(value);
    }
    return form;
}

// Where a channel sends to, in a few words, for the list.
export function destination(channel: Channel): string {
    const settings = channel.settings as Record<string, string & string[]>;
    switch (channel.kind) {
    case "telegram":
        return settings.chat_id;
    case "webhook":
        return settings.url;
    case "smtp":
        return (settings.recipients ?? []).join(", ");
    case "matrix":
        return settings.room_id;
    }
}
