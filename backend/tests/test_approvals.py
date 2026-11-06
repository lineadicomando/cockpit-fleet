"""Approvals: a run of a template that needs one waits, prepared, until
another user approves it, and ends without starting if rejected or late."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from test_notify import START, NotifyBase

from cockpit_fleet import agents, approvals, cli, db, jobs, paths, schedules  # noqa: F401
from cockpit_fleet.auth import Caller, allowed, resolve_caller, rights_from_groups


class ApprovalBase(NotifyBase):
    def as_(self, name, params=None, user="anna", role="operator", rights=(), via=None):
        ctx = cli.Context(Caller(1000, user, role, frozenset(rights)), self.root / "fleet.db",
                          self.root / "config.toml")
        ctx._config = self.config
        self.addCleanup(ctx.close)
        data = {**(params or {}), **({"via": via} if via else {})}
        return cli.dispatch(name, json.dumps(data).encode(), ctx)

    def ok_as(self, name, params=None, **who):
        answer = self.as_(name, params, **who)
        self.assertTrue(answer["ok"], answer)
        return answer["data"]

    def code_as(self, name, params=None, **who):
        answer = self.as_(name, params, **who)
        self.assertFalse(answer["ok"], answer)
        return answer["error"]["code"]

    def waiting(self, **template):
        template = self.template(needs_approval=True, **template)
        run = self.ok_as("run.start", {"template_id": template["id"], "limit": "a"},
                         user="olga", role="operator")
        self.assertEqual(run["status"], "pending_approval")
        return run


class ApprovalTest(ApprovalBase):
    def test_a_run_waits_prepared_until_another_user_approves_it(self):
        run = self.waiting()
        self.assertEqual(self.started, [])
        self.assertTrue((paths.RUNS_DIR / run["id"] / "spec.json").is_file())
        # Not even an administrator's dispatch starts it.
        jobs.dispatch(self.ctx())
        self.assertEqual(self.started, [])

        listed = self.ok_as("approval.list", user="piero", role="viewer",
                            rights=("approve",))["approvals"]
        self.assertEqual([entry["id"] for entry in listed], [run["id"]])
        entry = listed[0]
        self.assertEqual((entry["template"], entry["options"]["limit"], entry["can_decide"]),
                         ("deploy", "a", True))
        self.assertEqual(entry["approval"]["requested_by"], "olga")
        self.assertEqual(entry["approval"]["expires_at"], START + 24 * 3600)

        # Who launched it cannot approve it, even with the right to approve.
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="olga",
                                      rights=("approve",)), "forbidden")
        self.assertFalse(self.ok_as("run.get", {"id": run["id"]}, user="olga")
                         ["approval"]["can_decide"])
        # An operator without the right cannot either.
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero"),
                         "forbidden")

        approved = self.ok_as("approval.approve", {"id": run["id"], "comment": " ok "},
                              user="piero", role="viewer", rights=("approve",))
        self.assertEqual(approved["status"], "running")
        self.assertEqual(self.started, [run["id"]])
        self.assertEqual((approved["approval"]["decision"], approved["approval"]["decided_by"],
                          approved["approval"]["comment"]), ("approved", "piero", "ok"))
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero",
                                      rights=("approve",)), "conflict")
        self.finish(run["id"])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "successful")
        audit = self.ok("audit.list")["entries"]
        self.assertIn(("piero", "approval.approve", "ok"),
                      [(entry["user"], entry["command"], entry["outcome"]) for entry in audit])

    def test_an_administrator_may_approve_their_own_launch(self):
        template = self.template(needs_approval=True)
        run = self.ok_as("run.start", {"template_id": template["id"]}, user="anna", role="admin")
        self.assertEqual(self.ok_as("approval.approve", {"id": run["id"]}, user="anna",
                                    role="admin")["status"], "running")

    def test_rejected_run_ends_without_starting_and_leaves_no_secret(self):
        run = self.waiting()
        directory = paths.RUNS_DIR / run["id"]
        rejected = self.ok_as("approval.reject", {"id": run["id"], "comment": "not today"},
                              user="piero", role="admin")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["error"], "rejected by piero: not today")
        self.assertFalse(directory.exists())
        self.assertEqual(self.started, [])
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero",
                                      role="admin"), "conflict")
        decided = self.ok("approval.list", {"state": "decided"})["approvals"]
        self.assertEqual([(entry["id"], entry["approval"]["decision"]) for entry in decided],
                         [(run["id"], "rejected")])

    def test_an_unapproved_run_expires(self):
        self.config = replace(self.config, approvals_expire_hours=2)
        run = self.waiting()
        self.clock += 2 * 3600 - 1
        self.assertEqual(approvals.expire(self.ctx()), 0)
        self.clock += 1
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero",
                                      role="admin"), "conflict")
        got = self.ok("run.get", {"id": run["id"]})
        self.assertEqual((got["status"], got["approval"]["decision"], got["error"]),
                         ("expired", "expired", "not approved in time"))
        self.assertFalse((paths.RUNS_DIR / run["id"]).exists())

    def test_the_tick_expires_what_waits(self):
        self.config = replace(self.config, approvals_expire_hours=1)
        run = self.waiting()
        self.clock += 3600
        with mock.patch.object(paths, "SCHEDULER_LOCK", self.root / "tick.lock"):
            schedules.tick(self.ctx())
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "expired")

    def test_a_waiting_run_can_be_canceled(self):
        run = self.waiting()
        self.assertEqual(self.ok_as("run.cancel", {"id": run["id"]}, user="olga")["status"],
                         "canceled")
        self.assertFalse((paths.RUNS_DIR / run["id"]).exists())
        self.assertEqual(self.ok("approval.list")["approvals"], [])
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero",
                                      role="admin"), "conflict")

    def test_a_run_canceled_while_it_waited_does_not_expire(self):
        self.config = replace(self.config, approvals_expire_hours=1)
        run = self.waiting()
        self.ok_as("run.cancel", {"id": run["id"]}, user="olga")
        self.clock += 3600
        self.assertEqual(approvals.expire(self.ctx()), 0)
        got = self.ok("run.get", {"id": run["id"]})
        self.assertEqual((got["status"], got["approval"]["decision"]), ("canceled", ""))
        decided = self.ok("approval.list", {"state": "decided"})["approvals"]
        self.assertEqual([entry["status"] for entry in decided], ["canceled"])

    def test_an_approver_without_a_role_sees_only_what_is_to_approve(self):
        run = self.waiting()
        who = {"user": "piero", "role": None, "rights": ("approve",)}
        session = self.ok_as("whoami", **who)
        self.assertEqual(session["rights"], ["approve"])
        self.assertEqual(sorted(session["commands"]),
                         ["approval.approve", "approval.list", "approval.reject", "whoami"])
        self.assertEqual(self.code_as("run.list", **who), "forbidden")
        self.assertEqual(self.code_as("run.get", {"id": run["id"]}, **who), "forbidden")
        self.assertEqual(len(self.ok_as("approval.list", **who)["approvals"]), 1)
        self.ok_as("approval.approve", {"id": run["id"]}, **who)
        # Nobody else gets the commands of approvals without the right.
        self.assertEqual(self.code_as("approval.list", user="x", role=None), "forbidden")

    def test_an_agent_never_approves(self):
        self.config = replace(self.config, mcp_enabled=True, mcp_max_role="admin")
        run = self.waiting()
        for who in ({"role": "admin"}, {"role": "viewer", "rights": ("approve",)}):
            self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero",
                                          via="mcp:test", **who), "forbidden", who)
        # Through the command fleet, a person does.
        self.ok_as("approval.approve", {"id": run["id"]}, user="piero", role="admin", via="cli")

    def test_a_refusal_by_the_command_itself_is_audited_and_agents_get_no_hidden_tool(self):
        run = self.waiting()
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="olga",
                                      rights=("approve",)), "forbidden")
        self.config = replace(self.config, mcp_enabled=True, mcp_max_role="admin")
        self.assertEqual(self.code_as("run.reconcile", {}, user="piero", role="admin",
                                      via="mcp:test"), "forbidden")
        self.ok_as("mcp.tools", {}, user="piero", role="viewer", via="mcp:test")
        audit = [(entry["user"], entry["command"], entry["outcome"])
                 for entry in self.ok("audit.list")["entries"]]
        self.assertEqual(audit.count(("olga", "approval.approve", "denied")), 1)
        self.assertEqual(audit.count(("piero", "run.reconcile", "denied")), 1)

    def test_deciding_is_not_a_tool_reading_is(self):
        for name in ("approval.approve", "approval.reject"):
            self.assertFalse(cli.COMMANDS[name].tool, name)
        self.assertTrue(cli.COMMANDS["approval.list"].tool)

    def test_the_rules_tell_who_can_approve(self):
        channel = self.channel()
        self.rule(channel, events=("approval",))
        run = self.waiting()
        rows = self.ctx().conn.execute("SELECT event, run_id FROM notifications").fetchall()
        self.assertEqual([(row["event"], row["run_id"]) for row in rows],
                         [("approval", run["id"])])
        payload = json.loads(self.ctx().conn.execute(
            "SELECT payload FROM notifications").fetchone()["payload"])
        self.assertEqual(payload["status"], "pending_approval")

    def test_rejected_runs_are_told_as_canceled(self):
        channel = self.channel()
        self.rule(channel, events=("canceled",))
        run = self.waiting()
        self.ok_as("approval.reject", {"id": run["id"]}, user="piero", role="admin")
        rows = self.ctx().conn.execute("SELECT event FROM notifications").fetchall()
        self.assertEqual([row["event"] for row in rows], ["canceled"])

    def test_template_says_whether_it_needs_approval_and_exports_it(self):
        template = self.template(needs_approval=True)
        self.assertTrue(self.ok("template.list", role="viewer")["templates"][0]["needs_approval"])
        document = self.ok("config.export")
        self.assertTrue(document["templates"][0]["needs_approval"])
        updated = self.ok("template.update", {**{key: template[key] for key in (
            "id", "name", "project_id", "playbook", "inventory_id")}})
        self.assertFalse(updated["needs_approval"])

    def test_only_an_administrator_turns_on_a_schedule_whose_runs_need_an_approval(self):
        template = self.template(needs_approval=True)
        analyze = mock.patch.object(schedules, "analyze",
                                    lambda calendar, timezone="", count=1:
                                    (calendar, [START + 3600 * (index + 1)
                                                for index in range(count)]))
        analyze.start()
        self.addCleanup(analyze.stop)
        schedule = self.ok("schedule.create", {"name": "nightly", "template_id": template["id"],
                                               "calendar": "daily", "enabled": False})
        self.assertEqual(self.code_as("schedule.enable", {"id": schedule["id"]}, user="olga",
                                      rights=("approve",)), "forbidden")
        self.ok_as("schedule.disable", {"id": schedule["id"]}, user="olga")
        self.assertTrue(self.ok_as("schedule.enable", {"id": schedule["id"]}, user="anna",
                                   role="admin")["enabled"])

    def test_a_schedule_counts_a_waiting_run_as_active(self):
        template = self.template(needs_approval=True)
        # No systemd-analyze needed (CI has none): one time, an hour on.
        analyze = mock.patch.object(schedules, "analyze",
                                    lambda calendar, timezone="", count=1:
                                    (calendar, [START + 3600 * (index + 1)
                                                for index in range(count)]))
        analyze.start()
        self.addCleanup(analyze.stop)
        schedule = self.ok("schedule.create", {"name": "nightly", "template_id": template["id"],
                                               "calendar": "daily", "overlap": "skip"})
        run = self.ok("schedule.run", {"id": schedule["id"]}, role="operator")
        self.assertEqual(run["status"], "pending_approval")
        self.assertEqual(schedules._active_runs(self.ctx(), schedule["id"]), [run["id"]])


class ModeTest(ApprovalBase):
    def launched(self, mode, user="olga", role="operator", **launch):
        if not hasattr(self, "by_mode"):
            self.by_mode = {}
        if not self.by_mode:
            self.by_mode[mode] = self.template(approval=mode)
        elif mode not in self.by_mode:
            first = next(iter(self.by_mode.values()))
            self.by_mode[mode] = self.ok("template.create", {
                **{key: first[key] for key in ("project_id", "playbook", "inventory_id")},
                "name": mode, "approval": mode})
        template = self.by_mode[mode]
        run = self.ok_as("run.start", {"template_id": template["id"], **launch}, user=user,
                         role=role)
        self.assertEqual(run["status"], "pending_approval")
        return run

    def test_a_confirmation_is_given_by_who_launched_it_too(self):
        run = self.launched("confirm")
        self.assertEqual(self.ok_as("run.get", {"id": run["id"]}, user="olga")["approval"]
                         ["mode"], "confirm")
        self.assertTrue(self.ok_as("run.get", {"id": run["id"]}, user="olga")["approval"]
                        ["can_decide"])
        # Not another operator, nor a viewer, without the right to approve.
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="piero"),
                         "forbidden")
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="olga",
                                      role="viewer"), "forbidden")
        approved = self.ok_as("approval.approve", {"id": run["id"]}, user="olga")
        self.assertEqual(approved["approval"]["decided_by"], "olga")
        # An approver may confirm it as well, and reject it.
        run = self.launched("confirm")
        self.ok_as("approval.reject", {"id": run["id"]}, user="piero", role="viewer",
                   rights=("approve",))

    def test_an_agent_does_not_confirm_even_its_own_run(self):
        self.config = replace(self.config, mcp_enabled=True, mcp_max_role="admin",
                              chat_profiles=("administrator",),
                              chat_default_profile="administrator")
        run = self.launched("confirm", via="mcp:test")
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="olga",
                                      via="mcp:test"), "forbidden")
        self.ok_as("approval.approve", {"id": run["id"]}, user="olga")

    def test_another_person_and_whether_administrators_are_exempt(self):
        run = self.launched("other", user="anna", role="admin")
        self.assertEqual(self.ok_as("run.get", {"id": run["id"]}, user="anna", role="admin")
                         ["approval"]["mode"], "other")
        self.config = replace(self.config, approvals_admins_exempt=False)
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="anna",
                                      role="admin"), "forbidden")
        self.ok_as("approval.approve", {"id": run["id"]}, user="carlo", role="admin")
        self.config = replace(self.config, approvals_admins_exempt=True)
        run = self.launched("other", user="anna", role="admin")
        self.ok_as("approval.approve", {"id": run["id"]}, user="anna", role="admin")

    def test_none_and_what_is_given_for_compatibility(self):
        template = self.template(approval="none")
        self.assertEqual(self.ok("run.start", {"template_id": template["id"]})["status"],
                         "running")
        listed = self.ok("template.list")["templates"][0]
        self.assertEqual((listed["approval"], listed["needs_approval"]), ("none", False))
        base = {key: template[key] for key in ("id", "name", "project_id", "playbook",
                                               "inventory_id")}
        for given, mode in (({"needs_approval": True}, "other"),
                            ({"approval": "confirm", "needs_approval": True}, "confirm"),
                            ({"approval": "confirm"}, "confirm"), ({}, "none")):
            updated = self.ok("template.update", {**base, **given})
            self.assertEqual((updated["approval"], updated["needs_approval"]),
                             (mode, mode != "none"), given)
        for bad in ({"approval": "maybe"}, {"approval": "none", "needs_approval": True},
                    {"approval": "other", "needs_approval": False}):
            self.assertEqual(self.code("template.update", {**base, **bad}), "bad_request", bad)

    def test_a_run_of_a_schedule_in_confirm_waits_for_another_person(self):
        template = self.template(approval="confirm")
        analyze = mock.patch.object(schedules, "analyze",
                                    lambda calendar, timezone="", count=1:
                                    (calendar, [START + 3600 * (index + 1)
                                                for index in range(count)]))
        analyze.start()
        self.addCleanup(analyze.stop)
        schedule = self.ok("schedule.create", {"name": "nightly", "template_id": template["id"],
                                               "calendar": "daily", "enabled": False})
        run = self.ok_as("schedule.run", {"id": schedule["id"]}, user="olga")
        self.assertEqual(self.ok_as("run.get", {"id": run["id"]}, user="olga")["approval"]
                         ["mode"], "other")
        self.assertEqual(self.code_as("approval.approve", {"id": run["id"]}, user="olga"),
                         "forbidden")
        # And only an administrator turns such a schedule on.
        self.assertEqual(self.code_as("schedule.enable", {"id": schedule["id"]}, user="olga"),
                         "forbidden")

    def test_a_template_exports_its_mode_for_old_and_new_controllers(self):
        self.template(approval="confirm")
        document = self.ok("config.export")
        exported = document["templates"][0]
        self.assertEqual((exported["approval"], exported["needs_approval"]), ("confirm", True))
        self.ok("template.delete", {"id": self.ok("template.list")["templates"][0]["id"]})
        self.assertEqual(self.ok("config.import", {"document": document})["failed"], 0)
        self.assertEqual(self.ok("template.list")["templates"][0]["approval"], "confirm")

    def test_the_strongest_mode_wins(self):
        self.assertEqual(approvals.strongest("none", "confirm"), "confirm")
        self.assertEqual(approvals.strongest("other", "confirm"), "other")
        self.assertEqual(approvals.strongest("none", "none"), "none")


class RightsTest(unittest.TestCase):
    def test_rights_come_from_their_groups_and_add_to_a_role(self):
        self.assertEqual(rights_from_groups(["cockpit-fleet-approver", "users"]), {"approve"})
        self.assertEqual(rights_from_groups(["cockpit-fleet-admin"]), frozenset())
        users = {1000: type("Entry", (), {"pw_name": "anna", "pw_gid": 1000})}
        caller = resolve_caller({"SUDO_UID": "1000"}, 982, users.__getitem__,
                                lambda user, gid: ["cockpit-fleet-approver",
                                                   "cockpit-fleet-viewer"])
        self.assertEqual((caller.role, caller.rights), ("viewer", {"approve"}))

    def test_a_command_is_allowed_by_its_role_or_its_right(self):
        approver = Caller(1, "a", None, frozenset({"approve"}))
        self.assertTrue(allowed(approver, "admin", "approve"))
        self.assertFalse(allowed(approver, "viewer"))
        self.assertTrue(allowed(Caller(1, "a", "admin"), "admin", "approve"))
        self.assertFalse(allowed(Caller(1, "a", "operator"), "admin", "approve"))


class MigrationTest(unittest.TestCase):
    def test_runs_keep_their_rows_and_cursor_across_the_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.db"
            conn = sqlite3.connect(path, isolation_level=None)
            for statements in db.MIGRATIONS[:15]:
                for statement in statements:
                    conn.execute(statement)
            conn.execute("PRAGMA user_version = 15")
            conn.execute("INSERT INTO runs (rowid, id, kind, status, title, created_by,"
                         " created_at, priority, pruned_at) VALUES"
                         " (7, 'r1', 'playbook', 'successful', 'x', 'anna', 1, 3, 2)")
            conn.close()
            conn = db.connect(path)
            row = conn.execute("SELECT rowid, * FROM runs").fetchone()
            self.assertEqual((row["rowid"], row["id"], row["priority"], row["pruned_at"]),
                             (7, "r1", 3, 2))
            conn.execute("UPDATE runs SET status = 'pending_approval'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE runs SET status = 'waiting'")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            conn.close()


if __name__ == "__main__":
    unittest.main()
