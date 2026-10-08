import unittest
from types import SimpleNamespace

from cockpit_fleet.auth import resolve_caller, role_at_least, role_from_groups

USERS = {
    0: SimpleNamespace(pw_name="root", pw_gid=0),
    1000: SimpleNamespace(pw_name="anna", pw_gid=1000),
}


def getpwuid(uid):
    return USERS[uid]


def groups(*names):
    return lambda user, gid: list(names)


class RoleTest(unittest.TestCase):
    def test_order(self):
        self.assertTrue(role_at_least("admin", "viewer"))
        self.assertTrue(role_at_least("operator", "operator"))
        self.assertFalse(role_at_least("viewer", "operator"))
        self.assertFalse(role_at_least(None, "viewer"))

    def test_highest_group_wins(self):
        self.assertEqual(role_from_groups(["users", "cockpit-fleet-viewer"]), "viewer")
        self.assertEqual(
            role_from_groups(["cockpit-fleet-viewer", "cockpit-fleet-admin", "cockpit-fleet-operator"]),
            "admin",
        )
        self.assertIsNone(role_from_groups(["sudo", "cockpit-fleet"]))


class ResolveCallerTest(unittest.TestCase):
    def resolve(self, env, group_names=None):
        return resolve_caller(env, 982, getpwuid, group_names or groups())

    def test_member_of_a_role_group(self):
        caller = self.resolve({"SUDO_UID": "1000"}, groups("cockpit-fleet-operator"))
        self.assertEqual((caller.uid, caller.user, caller.role), (1000, "anna", "operator"))

    def test_user_without_role_group_has_no_role(self):
        self.assertIsNone(self.resolve({"SUDO_UID": "1000"}, groups("sudo")).role)

    def test_root_is_admin_whatever_its_groups(self):
        self.assertEqual(self.resolve({"SUDO_UID": "0"}).role, "admin")

    def test_without_sudo_it_is_the_service_account_itself(self):
        caller = self.resolve({})
        self.assertEqual((caller.uid, caller.user, caller.role), (982, "system", "admin"))

    def test_unknown_or_malformed_uid_has_no_role(self):
        for value in ("4242", "abc", "", "1000 0"):
            self.assertIsNone(self.resolve({"SUDO_UID": value}).role, value)


if __name__ == "__main__":
    unittest.main()
