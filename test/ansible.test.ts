import { test } from "node:test";
import assert from "node:assert/strict";
import { indentAfter, placeAt, resolveModule, suggest, type ModuleDoc } from "../src/lib/ansible.ts";

const PLAYBOOK = `---
# The site
- name: Web
  hosts: web
  become: true
  roles:
    - role: common
      tags: [x]
  tasks:
    - name: Copy a file
      ansible.builtin.copy:
        dest: /etc/motd
        mode: "0644"
      when: ready
    - block:
        - debug:
            msg: hi
      vars:
        a: 1
`;

// The text up to the first line that starts with `line`, then `typed` in its place.
const upTo = (line: string, typed: string) => PLAYBOOK.slice(0, PLAYBOOK.indexOf(line)) + typed;
const at = (line: string, typed: string, path = "site.yml") => {
    const place = placeAt(upTo(line, typed), path);
    return place && [place.kind, place.scope, place.module, place.key, place.prefix].join(" ");
};

test("the keys of a play, of a task and of a role are told apart", () => {
    assert.equal(at("  become: true", "  gath"), "key play   gath");
    assert.equal(at("- name: Web", "- ho"), "key play   ho");
    assert.equal(at("      when: ready", "      regi"), "key task   regi");
    assert.equal(at("    - block:", "    - "), "key task   ");
    assert.equal(at("      tags: [x]", "      wh"), "key role   wh");
    assert.equal(at("    - role: common", "    - ro"), "key role   ro");
    // Inside a block the items are tasks again.
    assert.equal(at("        - debug:", "        - na"), "key task   na");
});

test("under a module the keys are its parameters", () => {
    assert.equal(at("        mode:", "        ow"), "key args ansible.builtin.copy  ow");
    assert.equal(at("            msg: hi", "            "), "key args debug  ");
    assert.equal(at("        mode:", "        backup: "), "value args ansible.builtin.copy backup ");
    assert.equal(at("        mode:", "        backup: tr"), "value args ansible.builtin.copy backup tr");
});

test("values of keywords, and places where nothing is suggested", () => {
    assert.equal(at("  become: true", "  become: "), "value play  become ");
    // The variables of a task, the middle of a value, a comment, a file of variables.
    assert.equal(at("        a: 1", "        b"), null);
    assert.equal(at("      when: ready", "      when: ready and x"), null);
    assert.equal(at("  hosts: web", "  # ho"), null);
    assert.equal(placeAt("a: 1\nb", "group_vars/all.yml"), null);
    assert.equal(placeAt("all:\n  hosts:\n    w", "inventories/hosts.yml"), null);
});

test("a file of tasks is a list of tasks from its first line", () => {
    assert.equal(placeAt("- na", "roles/web/tasks/main.yml")?.scope, "task");
    assert.equal(placeAt("---\n- name: x\n  ansible.builtin.file:\n    pa", "roles/web/handlers/main.yml")?.module, "ansible.builtin.file");
    assert.equal(placeAt("- na", "site.yml")?.scope, "play");
    // A list written at the indentation of its key.
    assert.equal(placeAt("- hosts: all\n  tasks:\n  - na", "site.yml")?.scope, "task");
});

const MODULES = { "ansible.builtin.copy": "Copy files", "community.general.copy_x": "Other" };
const COPY: ModuleDoc = {
    name: "ansible.builtin.copy", description: "Copy files", options: [
        { name: "backup", type: "bool", required: false, default: false, choices: [], aliases: [], description: "Keep a backup." },
        { name: "dest", type: "path", required: true, default: null, choices: [], aliases: ["path"], description: "Where." },
        { name: "state", type: "str", required: false, default: "file", choices: ["file", "absent"], aliases: [], description: "" },
    ],
};

test("suggestions: keywords and modules for a task, parameters for a module", () => {
    const place = (typed: string, path = "roles/x/tasks/main.yml") => placeAt(typed, path)!;
    const task = suggest(place("- "), MODULES);
    assert.ok(task.some(item => item.label === "register" && item.apply === "register: " && item.first));
    assert.deepEqual(task.filter(item => item.kind === "module").map(item => [item.label, item.apply, item.info]),
        [["ansible.builtin.copy", "ansible.builtin.copy:", "Copy files"], ["community.general.copy_x", "community.general.copy_x:", "Other"]]);
    assert.ok(!suggest(place("- ", "site.yml"), MODULES).some(item => item.kind === "module"));
    assert.ok(suggest(place("- ", "site.yml")).some(item => item.label === "hosts"));

    const args = suggest(place("- copy:\n    "), MODULES, COPY);
    assert.deepEqual(args.map(item => [item.label, item.detail, item.first]),
        [["backup", "bool, = false", false], ["dest", "path, required", true], ["state", "str, = \"file\"", false]]);
    assert.deepEqual(suggest(place("- copy:\n    "), MODULES, null), []);

    const labels = (typed: string, path?: string) => suggest(place(typed, path), MODULES, COPY).map(item => item.label);
    assert.deepEqual(labels("- copy:\n    backup: "), ["true", "false"]);
    assert.deepEqual(labels("- copy:\n    state: "), ["file", "absent"]);
    assert.deepEqual(labels("- copy:\n    path: "), []);
    assert.deepEqual(labels("- become: "), ["true", "false"]);
    assert.deepEqual(labels("- name: "), []);
});

test("resolveModule accepts the short name of a builtin module", () => {
    assert.equal(resolveModule("ansible.builtin.copy", MODULES), "ansible.builtin.copy");
    assert.equal(resolveModule("copy", MODULES), "ansible.builtin.copy");
    assert.equal(resolveModule("copy_x", MODULES), null);
    assert.equal(resolveModule("toString", MODULES), null);
});

test("indentAfter follows the structure of YAML", () => {
    assert.equal(indentAfter("- hosts: all"), 2);
    assert.equal(indentAfter("  tasks:"), 4);
    assert.equal(indentAfter("    - name: x"), 6);
    assert.equal(indentAfter("      ansible.builtin.copy:  # copy"), 8);
    assert.equal(indentAfter("        content: |"), 10);
    assert.equal(indentAfter("    - block:"), 8);
    assert.equal(indentAfter("        mode: \"0644\""), 8);
    assert.equal(indentAfter("  url: http://x"), 2);
    assert.equal(indentAfter(""), 0);
});
