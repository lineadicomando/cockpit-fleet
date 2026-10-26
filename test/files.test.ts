import { test } from "node:test";
import assert from "node:assert/strict";
import { ancestors, buildTree, inventoryChoices, isUsablePath, isYaml, matchPaths, playbookChoices } from "../src/lib/files.ts";

test("buildTree nests paths, directories first and each level by name", () => {
    const tree = buildTree(["site.yml", "roles/web/tasks/main.yml", "ansible.cfg", "roles/db/defaults/main.yml", "group_vars/all.yml"]);
    assert.deepEqual(tree.map(node => node.name), ["group_vars", "roles", "ansible.cfg", "site.yml"]);
    const roles = tree[1];
    assert.deepEqual(roles.children!.map(node => node.path), ["roles/db", "roles/web"]);
    assert.deepEqual(roles.children![1].children![0].children, [{ name: "main.yml", path: "roles/web/tasks/main.yml" }]);
    assert.equal(tree[3].children, undefined);
    assert.deepEqual(buildTree([]), []);
});

test("ancestors lists the directories a file is in, outermost first", () => {
    assert.deepEqual(ancestors("roles/web/tasks/main.yml"), ["roles", "roles/web", "roles/web/tasks"]);
    assert.deepEqual(ancestors("site.yml"), []);
});

test("isYaml and isUsablePath", () => {
    assert.ok(isYaml("site.yml") && isYaml("a/b.yaml"));
    assert.ok(!isYaml("hosts") && !isYaml("yml"));
    for (const path of ["site.yml", "roles/web/tasks/main.yml", "with space/è.yml"]) assert.ok(isUsablePath(path), path);
    for (const path of ["", "/etc/passwd", "a//b", "a/../b", "./a", "a/", " a", ".git/config", "a/.GIT/x"]) assert.ok(!isUsablePath(path), path);
});

test("inventoryChoices and playbookChoices leave out group_vars and host_vars", () => {
    const files = ["site.yml", "inv/prod/hosts.ini", "inv/prod/group_vars/all.yml", "host_vars/web.yml"];
    const directories = ["inv", "inv/prod", "inv/prod/group_vars", "host_vars"];
    assert.deepEqual(inventoryChoices(files, directories), [
        { path: "inv", directory: true },
        { path: "inv/prod", directory: true },
        { path: "inv/prod/hosts.ini", directory: false },
        { path: "site.yml", directory: false },
    ]);
    assert.deepEqual(playbookChoices(files), [{ path: "site.yml", directory: false }]);
});

test("matchPaths puts the start of a path first, then the start of a part", () => {
    const choices = ["playbooks/site.yml", "site.yml", "inventories/production/hosts.yml", "prod.yml"]
            .map(path => ({ path, directory: false }));
    const paths = (typed: string, limit = 10) => matchPaths(choices, typed, limit).shown.map(choice => choice.path);
    assert.deepEqual(paths("site"), ["site.yml", "playbooks/site.yml"]);
    assert.deepEqual(paths("PROD"), ["prod.yml", "inventories/production/hosts.yml"]);
    assert.deepEqual(paths("ost"), ["inventories/production/hosts.yml"]);
    assert.deepEqual(paths(""), choices.map(choice => choice.path));
    assert.deepEqual(matchPaths(choices, "", 3).more, 1);
    assert.deepEqual(paths("nothing"), []);
});
