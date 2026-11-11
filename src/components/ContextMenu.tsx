import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Divider, Menu, MenuContent, MenuItem, MenuList } from "@patternfly/react-core";

export type MenuEntry = { title: string; onClick: () => void; isDanger?: boolean; isDisabled?: boolean } | { isSeparator: true };

// A menu where the pointer was, or beside the row it is for when opened
// from the keyboard (Shift+F10, the Menu key). The arrow keys go through
// its items; Escape, Tab, a click elsewhere or a scroll close it, and the
// focus goes back where it was.
export function ContextMenu({ x, y, label, items, onClose }: {
    x: number;
    y: number;
    label: string;
    items: MenuEntry[];
    onClose: () => void;
}) {
    const box = useRef<HTMLDivElement>(null);
    const [place, setPlace] = useState({ left: x, top: y });
    const back = useRef<Element | null>(document.activeElement);
    const close = useRef(onClose);
    close.current = onClose;

    // Kept inside the window.
    useLayoutEffect(() => {
        const element = box.current;
        if (!element) return;
        const { width, height } = element.getBoundingClientRect();
        setPlace({
            left: Math.max(0, Math.min(x, window.innerWidth - width - 4)),
            top: Math.max(0, Math.min(y, window.innerHeight - height - 4)),
        });
        buttons()[0]?.focus();
    }, [x, y]);

    useEffect(() => {
        const away = (event: Event) => { if (!box.current?.contains(event.target as Node)) close.current(); };
        const shut = () => close.current();
        document.addEventListener("mousedown", away, true);
        document.addEventListener("scroll", shut, true);
        window.addEventListener("resize", shut);
        window.addEventListener("blur", shut);
        const previous = back.current;
        return () => {
            document.removeEventListener("mousedown", away, true);
            document.removeEventListener("scroll", shut, true);
            window.removeEventListener("resize", shut);
            window.removeEventListener("blur", shut);
            if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
        };
    }, []);

    function buttons(): HTMLElement[] {
        return Array.from(box.current?.querySelectorAll<HTMLElement>("button:not(:disabled)") ?? []);
    }

    function onKeyDown(event: React.KeyboardEvent) {
        const all = buttons();
        const at = all.indexOf(document.activeElement as HTMLElement);
        let next: HTMLElement | undefined;
        switch (event.key) {
        case "ArrowDown": next = all[(at + 1) % all.length]; break;
        case "ArrowUp": next = all[(at - 1 + all.length) % all.length]; break;
        case "Home": next = all[0]; break;
        case "End": next = all[all.length - 1]; break;
        case "Escape":
        case "Tab":
            event.preventDefault();
            event.stopPropagation();
            onClose();
            return;
        default: return;
        }
        event.preventDefault();
        event.stopPropagation();
        next?.focus();
    }

    return (
        <div ref={box} className="fleet-context-menu" style={{ left: place.left, top: place.top }} onKeyDown={onKeyDown}
            onContextMenu={event => event.preventDefault()}>
            <Menu aria-label={label} isPlain={false}>
                <MenuContent>
                    <MenuList>
                        {items.map((item, index) => ("isSeparator" in item
                            ? <Divider key={index} component="li" />
                            : (
                                <MenuItem key={index} isDanger={item.isDanger} isDisabled={item.isDisabled}
                                    onClick={() => { onClose(); item.onClick(); }}>
                                    {item.title}
                                </MenuItem>
                            )))}
                    </MenuList>
                </MenuContent>
            </Menu>
        </div>
    );
}
