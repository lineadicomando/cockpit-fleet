"""Export and import of the configuration, between two empty installations."""

import json
import tempfile
from pathlib import Path

from test_phase2 import Base

from cockpit_fleet import paths, transfer
from cockpit_fleet.auth import Caller
from cockpit_fleet.cli import Context, dispatch

KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIA=="


class TransferTest(Base):
    def populate(self):
        machine = self.ok("credential.create", {"name": "ssh", "kind": "machine",
                                                "username": "maint"})
        vault = self.ok("credential.create", {"name": "vault", "kind": "vault", "vault_id": "prod"})
        source = self.ok("credential.create", {"name": "token", "kind": "source"})
        (paths.SECRETS_DIR / f"c-{machine['id']}-password.cred").write_bytes(b"TopSecretCipher")
        project = self.local_project()
        self.ok("project.create", {"name": "git", "kind": "git", "ref": "v1",
                                   "url": "https://example.org/a/b.git",
                                   "credential_id": source["id"]})
        managed = self.ok("inventory.create", {"name": "inv", "kind": "managed", "format": "ini",
                                               "content": "[all]\na\n",
                                               "vault_credential_id": vault["id"]})
        self.ok("inventory.create", {"name": "file", "kind": "file", "project_id": project["id"],
                                     "path": "inventories/hosts.yml"})
        self.ok("hostkey.add", {"host": "10.0.0.5", "key_type": "ssh-ed25519", "key": KEY})
        self.ok("template.create", {
            "name": "deploy", "project_id": project["id"], "playbook": "site.yml",
            "inventory_id": managed["id"], "machine_credential_id": machine["id"],
            "vault_credential_ids": [vault["id"]], "limit_hosts": "web*", "tags": ["a"],
            "extra_vars": {"x": 1}, "forks": 7, "become": True,
            "survey": [{"name": "version", "type": "text", "required": True}]})

    def elsewhere(self):
        """A context on another, empty database."""
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        ctx = Context(Caller(1000, "anna", "admin"), Path(other.name) / "fleet.db",
                      self.root / "config.toml")
        ctx._config = self.config
        return ctx

    def test_export_has_names_and_no_secret(self):
        self.populate()
        document = self.ok("config.export")
        text = json.dumps(document)
        self.assertEqual((document["format"], document["version"]), ("cockpit-fleet-config", 1))
        self.assertEqual([c["name"] for c in document["credentials"]], ["ssh", "token", "vault"])
        self.assertNotIn("TopSecretCipher", text)
        self.assertNotIn("secret", text)
        template = document["templates"][0]
        self.assertEqual((template["project"], template["inventory"], template["machine_credential"],
                          template["vault_credentials"]), ("site", "inv", "ssh", ["vault"]))
        self.assertEqual(document["projects"][0]["credential"], "token")
        self.assertEqual(self.code("config.export", role="operator"), "forbidden")

    def test_import_recreates_everything_and_is_repeatable(self):
        self.populate()
        document = self.ok("config.export")
        ctx = self.elsewhere()
        answer = dispatch("config.import", json.dumps({"document": document}).encode(), ctx)
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertEqual((data["created"], data["exists"], data["failed"]), (9, 0, 0), data)
        again = dispatch("config.export", b"", ctx)["data"]
        for section in transfer.SECTIONS:
            self.assertEqual(again[section], document[section], section)
        second = dispatch("config.import", json.dumps({"document": document}).encode(), ctx)["data"]
        self.assertEqual((second["created"], second["exists"], second["failed"]), (0, 9, 0))
        # Each thing left the record its own command leaves; the text of an
        # inventory is in none.
        rows = ctx.conn.execute("SELECT command, params FROM audit").fetchall()
        commands = [row["command"] for row in rows]
        self.assertEqual(commands.count("template.create"), 1)
        self.assertEqual(commands.count("config.import"), 2)
        self.assertNotIn("[all]", " ".join(row["params"] for row in rows))

    def test_one_failure_does_not_stop_the_rest(self):
        document = {"format": "cockpit-fleet-config", "version": 1,
                    "credentials": [{"name": "ok", "kind": "vault"}, {"name": "bad", "kind": "x"},
                                    "nonsense"],
                    "projects": [{"name": "elsewhere", "kind": "local", "path": "/etc"}],
                    "inventories": [{"name": "orphan", "kind": "file", "project": "missing",
                                     "path": "hosts"}],
                    "host_keys": [{"host": "10.0.0.5", "key_type": "ssh-ed25519", "key": KEY},
                                  {"host": "-oProxyCommand=id", "key_type": "ssh-rsa",
                                   "key": KEY}],
                    "templates": [{"name": "t", "project": "elsewhere", "playbook": "site.yml",
                                   "inventory": "orphan"}]}
        data = self.ok("config.import", {"document": document})
        outcomes = {(entry["section"], entry["name"]): entry["outcome"] for entry in data["report"]}
        self.assertEqual(outcomes[("credentials", "ok")], "created")
        self.assertEqual(outcomes[("host_keys", "10.0.0.5")], "created")
        self.assertEqual((data["created"], data["failed"]), (2, 6))
        self.assertIn("no project called 'missing'",
                      [e["detail"] for e in data["report"] if e["name"] == "orphan"][0])

    def test_a_changed_host_key_is_not_replaced(self):
        self.ok("hostkey.add", {"host": "10.0.0.5", "key_type": "ssh-ed25519", "key": KEY})
        other = "AAAAC3NzaC1lZDI1NTE5AAAAIB=="
        data = self.ok("config.import", {"document": {
            "format": "cockpit-fleet-config", "version": 1,
            "host_keys": [{"host": "10.0.0.5", "key_type": "ssh-ed25519", "key": other}]}})
        self.assertEqual(data["failed"], 1)
        keys = self.ctx().conn.execute("SELECT key FROM host_keys").fetchall()
        self.assertEqual([row["key"] for row in keys], [KEY])

    def test_documents_of_another_kind_are_refused(self):
        for document in ({}, {"format": "cockpit-fleet-config", "version": 2}, {"version": 1}):
            self.assertEqual(self.code("config.import", {"document": document}), "bad_request")
        self.assertEqual(self.code("config.import", {"document": {
            "format": "cockpit-fleet-config", "version": 1}}, role="operator"), "forbidden")


if __name__ == "__main__":
    import unittest
    unittest.main()
