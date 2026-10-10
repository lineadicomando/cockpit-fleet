import { test } from "node:test";
import assert from "node:assert/strict";
import { ancestors, buildTree, isUsablePath, isYaml } from "../src/lib/files.ts";

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
