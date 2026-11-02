import { test } from "node:test";
import assert from "node:assert/strict";
import {
    ancestors, basename, buildTree, dirname, filterFiles, inventoryChoices, isInside, isUsablePath, isYaml, matchPaths,
    playbookChoices, relocate, renamed, topmost, visibleNodes,
} from "../src/lib/files.ts";

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

test("buildTree shows empty directories too", () => {
    const tree = buildTree(["site.yml"], ["group_vars/web", "files"]);
    assert.deepEqual(tree.map(node => node.path), ["files", "group_vars", "site.yml"]);
    assert.deepEqual(tree[1].children, [{ name: "web", path: "group_vars/web", children: [] }]);
});

test("basename, dirname and isInside", () => {
    assert.equal(basename("a/b/c.yml"), "c.yml");
    assert.equal(basename("c.yml"), "c.yml");
    assert.equal(dirname("a/b/c.yml"), "a/b");
    assert.equal(dirname("c.yml"), "");
    assert.ok(isInside("a/b", "a") && isInside("a", "a") && isInside("a", ""));
    assert.ok(!isInside("ab", "a") && !isInside("a", "a/b"));
});

test("topmost leaves out what is inside another", () => {
    assert.deepEqual(topmost(["a/b", "a", "c", "a/b/d", "c"]), ["a", "c"]);
});

test("relocate says where each goes, nothing into itself", () => {
    assert.deepEqual(relocate(["a/x.yml", "b"], "c"), [{ path: "a/x.yml", to: "c/x.yml" }, { path: "b", to: "c/b" }]);
    assert.deepEqual(relocate(["c/x.yml", "d/y"], "c"), [{ path: "d/y", to: "c/y" }]);
    assert.deepEqual(relocate(["a/x.yml"], ""), [{ path: "a/x.yml", to: "x.yml" }]);
    assert.equal(relocate(["a"], "a/b"), null);
    assert.equal(relocate(["a"], "a"), null);
});

test("renamed follows a path into its new place", () => {
    assert.equal(renamed("a/b/c.yml", "a/b", "x"), "x/c.yml");
    assert.equal(renamed("a/b", "a/b", "x"), "x");
    assert.equal(renamed("a/bc", "a/b", "x"), null);
});

test("filterFiles keeps what matches and opens its directories", () => {
    const { files, open } = filterFiles(["roles/web/tasks/main.yml", "site.yml", "group_vars/all.yml"], "WEB");
    assert.deepEqual(files, ["roles/web/tasks/main.yml"]);
    assert.deepEqual([...open], ["roles", "roles/web", "roles/web/tasks"]);
    assert.equal(filterFiles(["a"], " ").files.length, 1);
});

test("visibleNodes skips what is in closed directories", () => {
    const tree = buildTree(["a/b/c.yml", "a/d.yml", "e.yml"]);
    assert.deepEqual(visibleNodes(tree, new Set()).map(entry => entry.node.path), ["a", "e.yml"]);
    assert.deepEqual(visibleNodes(tree, new Set(["a"])).map(entry => `${entry.level}:${entry.node.path}`),
        ["1:a", "2:a/b", "2:a/d.yml", "1:e.yml"]);
});
