"""The store over SFTP: a client of the protocol, here, against store.sftp."""

import dataclasses
import os
import struct
import threading

from test_storage import StoreBase

from cockpit_fleet import cli, paths, sftp
from cockpit_fleet.sftp import (
    FXF_CREAT,
    FXF_EXCL,
    FXF_READ,
    FXF_TRUNC,
    FXF_WRITE,
)

WRITE_NEW = FXF_WRITE | FXF_CREAT | FXF_TRUNC


def s(value) -> bytes:
    data = value.encode() if isinstance(value, str) else value
    return struct.pack(">I", len(data)) + data


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def u64(value: int) -> bytes:
    return struct.pack(">Q", value)


NO_ATTRS = u32(0)


class Reply:
    def __init__(self, kind: int, data: bytes):
        self.kind, self.data, self.at = kind, data, 0

    def u32(self) -> int:
        value = struct.unpack(">I", self.data[self.at:self.at + 4])[0]
        self.at += 4
        return value

    def u64(self) -> int:
        value = struct.unpack(">Q", self.data[self.at:self.at + 8])[0]
        self.at += 8
        return value

    def bytes(self) -> bytes:
        size = self.u32()
        value = self.data[self.at:self.at + size]
        self.at += size
        return value

    def attrs(self) -> dict:
        flags = self.u32()
        found = {}
        if flags & sftp.ATTR_SIZE:
            found["size"] = self.u64()
        if flags & sftp.ATTR_PERMISSIONS:
            found["permissions"] = self.u32()
        if flags & sftp.ATTR_ACMODTIME:
            found["atime"], found["mtime"] = self.u32(), self.u32()
        return found


class Client:
    """One session, its server in a thread, as fleetctl would hold it."""

    def __init__(self, test, role="operator", user="anna"):
        to_server, self._to = os.pipe()
        self._from, from_server = os.pipe()
        self.to = os.fdopen(self._to, "wb")
        self.reader = os.fdopen(self._from, "rb")
        ctx = test.ctx(role, user)
        ctx.stdin = os.fdopen(to_server, "rb")
        ctx.replies = os.fdopen(from_server, "wb")
        self.answer = None
        self.next = 0

        def serve():
            try:
                self.answer = cli.dispatch("store.sftp", b"", ctx)
            finally:
                ctx.stdin.close()
                ctx.replies.close()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.packet(bytes([sftp.INIT]) + u32(3))
        version = self.read()
        test.assertEqual((version.kind, version.u32()), (sftp.VERSION_REPLY, 3))
        self.extensions = version.data[4:]

    def packet(self, data: bytes) -> None:
        self.to.write(u32(len(data)) + data)
        self.to.flush()

    def read(self) -> Reply:
        head = self.reader.read(4)
        if len(head) < 4:
            raise EOFError
        data = self.reader.read(struct.unpack(">I", head)[0])
        return Reply(data[0], data[1:])

    def call(self, kind: int, payload: bytes = b"") -> Reply:
        self.next += 1
        self.packet(bytes([kind]) + u32(self.next) + payload)
        reply = self.read()
        assert reply.u32() == self.next
        return reply

    def status(self, kind: int, payload: bytes = b"") -> int:
        reply = self.call(kind, payload)
        assert reply.kind == sftp.STATUS, reply.kind
        return reply.u32()

    def open(self, path: str, flags: int = FXF_READ, attrs: bytes = NO_ATTRS) -> bytes:
        reply = self.call(sftp.OPEN, s(path) + u32(flags) + attrs)
        if reply.kind != sftp.HANDLE:
            raise AssertionError(f"open {path}: status {reply.u32()} {reply.bytes()!r}")
        return reply.bytes()

    def write(self, handle: bytes, offset: int, data: bytes) -> int:
        return self.status(sftp.WRITE, s(handle) + u64(offset) + s(data))

    def close(self, handle: bytes) -> int:
        return self.status(sftp.CLOSE, s(handle))

    def put(self, path: str, data: bytes, flags: int = WRITE_NEW) -> int:
        handle = self.open(path, flags)
        for offset in range(0, len(data), 32768):
            assert self.write(handle, offset, data[offset:offset + 32768]) == sftp.OK
        return self.close(handle)

    def get(self, path: str) -> bytes:
        handle = self.open(path)
        data = b""
        while True:
            reply = self.call(sftp.READ, s(handle) + u64(len(data)) + u32(65536))
            if reply.kind == sftp.STATUS:
                assert reply.u32() == sftp.EOF
                break
            data += reply.bytes()
        self.close(handle)
        return data

    def stat(self, path: str) -> dict | int:
        reply = self.call(sftp.STAT, s(path))
        return reply.attrs() if reply.kind == sftp.ATTRS else reply.u32()

    def listdir(self, path: str) -> dict[str, tuple[str, dict]]:
        reply = self.call(sftp.OPENDIR, s(path))
        assert reply.kind == sftp.HANDLE, reply.u32()
        handle = reply.bytes()
        found = {}
        while True:
            reply = self.call(sftp.READDIR, s(handle))
            if reply.kind == sftp.STATUS:
                assert reply.u32() == sftp.EOF
                break
            for _ in range(reply.u32()):
                name = reply.bytes().decode()
                longname = reply.bytes().decode()
                found[name] = (longname, reply.attrs())
        self.close(handle)
        return found

    def rename(self, path: str, to: str, posix: bool = False) -> int:
        if posix:
            return self.status(sftp.EXTENDED, s(sftp.POSIX_RENAME) + s(path) + s(to))
        return self.status(sftp.RENAME, s(path) + s(to))

    def end(self):
        self.to.close()
        self.thread.join(10)
        self.reader.close()
        return self.answer


class SftpTest(StoreBase):
    def session(self, role="operator", user="anna"):
        client = Client(self, role, user)
        self.addCleanup(lambda: client.thread.is_alive() and client.end())
        return client

    def audit(self):
        return [(e["command"], e["outcome"], e["via"])
                for e in reversed(self.ok("audit.list")["entries"])]

    def test_a_file_written_reaches_the_store_only_when_closed(self):
        client = self.session()
        self.assertIn(sftp.POSIX_RENAME.encode(), client.extensions)
        self.assertEqual(client.status(sftp.MKDIR, s("/apps") + NO_ATTRS), sftp.OK)
        data = os.urandom(100_000)
        handle = client.open("/apps/setup.msi", WRITE_NEW)
        client.write(handle, 0, data[:50_000])
        self.assertFalse((paths.STORE_DIR / "apps/setup.msi").exists())
        client.write(handle, 50_000, data[50_000:])
        self.assertEqual(client.close(handle), sftp.OK)
        self.assertEqual((paths.STORE_DIR / "apps/setup.msi").read_bytes(), data)
        self.assertEqual(list(paths.STORE_UPLOADS.iterdir()), [])
        self.assertEqual(client.get("apps/setup.msi"), data)
        self.assertEqual(client.stat("/apps/setup.msi")["size"], len(data))
        listed = client.listdir("/apps")
        self.assertEqual(set(listed), {".", "..", "setup.msi"})
        self.assertTrue(listed["setup.msi"][0].startswith("-rw-r--r--"))
        self.assertEqual(client.end(), {"ok": True, "data": {}})
        row = self.listed()["apps/setup.msi"]
        self.assertEqual((row["origin"], row["updated_by"], row["size"]),
                         ("upload", "anna", len(data)))
        self.assertEqual(self.audit(), [("store.mkdir", "ok", "sftp"), ("store.put", "ok", "sftp"),
                                        ("store.read", "ok", "sftp"), ("store.sftp", "ok", "sftp")])

    def test_a_filepart_waits_is_resumed_and_is_renamed_over_the_old_file(self):
        self.by_hand("big.iso", b"old")
        data = os.urandom(200_000)
        client = self.session()
        handle = client.open("/big.iso.filepart", WRITE_NEW)
        client.write(handle, 0, data[:120_000])
        self.assertEqual(client.close(handle), sftp.OK)
        self.assertFalse((paths.STORE_DIR / "big.iso.filepart").exists())
        self.assertEqual(client.stat("/big.iso.filepart")["size"], 120_000)
        client.end()
        # Another session of the same user finds it and writes on from its end.
        client = self.session()
        self.assertIn("big.iso.filepart", client.listdir("/"))
        self.assertEqual(self.session(user="bruno").stat("/big.iso.filepart"), sftp.NO_SUCH_FILE)
        handle = client.open("/big.iso.filepart", FXF_WRITE)
        client.write(handle, 120_000, data[120_000:])
        mtime = 1_700_000_000
        self.assertEqual(client.status(sftp.FSETSTAT, s(handle) + u32(sftp.ATTR_ACMODTIME)
                                       + u32(mtime) + u32(mtime)), sftp.OK)
        client.close(handle)
        self.assertEqual(client.rename("/big.iso.filepart", "/big.iso"), sftp.FAILURE)
        self.assertEqual(client.rename("/big.iso.filepart", "/big.iso", posix=True), sftp.OK)
        self.assertEqual((paths.STORE_DIR / "big.iso").read_bytes(), data)
        self.assertEqual(int((paths.STORE_DIR / "big.iso").stat().st_mtime), mtime)
        self.assertEqual(list(paths.STORE_UPLOADS.iterdir()), [])
        # A time set afterwards, as WinSCP does, keeps what the row knows.
        later = mtime + 60
        self.assertEqual(client.status(sftp.SETSTAT, s("/big.iso") + u32(sftp.ATTR_ACMODTIME
                                       | sftp.ATTR_PERMISSIONS) + u32(0o600) + u32(later)
                                       + u32(later)), sftp.OK)
        self.assertEqual(int((paths.STORE_DIR / "big.iso").stat().st_mtime), later)
        client.end()
        self.assertEqual(self.listed()["big.iso"]["origin"], "upload")

    def test_a_session_that_ends_before_the_close_leaves_nothing_in_the_store(self):
        client = self.session()
        handle = client.open("/a.bin", WRITE_NEW)
        client.write(handle, 0, b"half")
        client.end()
        self.assertFalse((paths.STORE_DIR / "a.bin").exists())
        # The page does not offer to resume it: it is SFTP's.
        self.assertEqual(self.ok("store.uploads", role="operator")["uploads"], [])
        client = self.session()
        self.assertEqual(client.stat("/a.bin")["size"], 4)
        self.assertEqual(client.status(sftp.REMOVE, s("/a.bin")), sftp.OK)
        self.assertEqual(list(paths.STORE_UPLOADS.iterdir()), [])

    def test_viewers_read_and_are_refused_writes(self):
        self.by_hand("doc.txt", b"text")
        client = self.session(role="viewer")
        listed = client.listdir("/")
        self.assertTrue(listed["doc.txt"][0].startswith("-r--r--r--"))
        self.assertEqual(listed["doc.txt"][1]["permissions"] & 0o777, 0o444)
        self.assertEqual(client.get("/doc.txt"), b"text")
        reply = client.call(sftp.OPEN, s("/new.txt") + u32(WRITE_NEW) + NO_ATTRS)
        self.assertEqual((reply.kind, reply.u32()), (sftp.STATUS, sftp.PERMISSION_DENIED))
        for kind, payload in ((sftp.REMOVE, s("/doc.txt")), (sftp.MKDIR, s("/d") + NO_ATTRS),
                              (sftp.RENAME, s("/doc.txt") + s("/b.txt"))):
            self.assertEqual(client.status(kind, payload), sftp.PERMISSION_DENIED)
        client.end()
        self.assertEqual((paths.STORE_DIR / "doc.txt").read_bytes(), b"text")
        self.assertIn(("store.put", "denied", "sftp"), self.audit())

    def test_the_store_is_the_root_and_holds_no_way_out(self):
        self.by_hand("a.txt", b"a")
        (paths.STORE_DIR / "link").symlink_to("/etc")
        client = self.session()
        reply = client.call(sftp.REALPATH, s("../../.."))
        self.assertEqual((reply.u32(), reply.bytes()), (1, b"/"))
        self.assertEqual(set(client.listdir("/../..")), {".", "..", "a.txt"})
        self.assertEqual(client.stat("/../../etc/passwd"), sftp.NO_SUCH_FILE)
        self.assertEqual(client.stat("/link"), sftp.NO_SUCH_FILE)
        self.assertNotEqual(client.call(sftp.OPEN, s("/link/passwd") + u32(FXF_READ)
                                        + NO_ATTRS).kind, sftp.HANDLE)
        self.assertEqual(client.call(sftp.OPENDIR, s("/link")).kind, sftp.STATUS)
        self.assertEqual(client.status(sftp.SYMLINK, s("/x") + s("/etc")),
                         sftp.OP_UNSUPPORTED)
        reply = client.call(sftp.OPEN, s("/.git") + u32(WRITE_NEW) + NO_ATTRS)
        self.assertEqual(reply.kind, sftp.STATUS)

    def test_files_and_directories_are_moved_and_deleted_as_in_the_page(self):
        self.by_hand("d/a.txt", b"a")
        self.listed()
        client = self.session()
        # Written in place: refused; replaced whole: taken.
        reply = client.call(sftp.OPEN, s("/d/a.txt") + u32(FXF_WRITE) + NO_ATTRS)
        self.assertEqual((reply.kind, reply.u32()), (sftp.STATUS, sftp.OP_UNSUPPORTED))
        reply = client.call(sftp.OPEN, s("/d/a.txt") + u32(WRITE_NEW | FXF_EXCL) + NO_ATTRS)
        self.assertEqual((reply.kind, reply.u32()), (sftp.STATUS, sftp.FAILURE))
        self.assertEqual(client.put("/d/a.txt", b"new"), sftp.OK)
        reply = client.call(sftp.OPEN, s("/nowhere/a.txt") + u32(WRITE_NEW) + NO_ATTRS)
        self.assertEqual((reply.kind, reply.u32()), (sftp.STATUS, sftp.NO_SUCH_FILE))
        self.assertEqual(client.status(sftp.RMDIR, s("/d")), sftp.FAILURE)
        self.assertEqual(client.status(sftp.MKDIR, s("/e") + NO_ATTRS), sftp.OK)
        self.assertEqual(client.rename("/d", "/e"), sftp.FAILURE)
        self.assertEqual(client.rename("/d", "/d/inner"), sftp.FAILURE)
        self.assertEqual(client.rename("/d", "/e/d"), sftp.OK)
        self.assertEqual(client.status(sftp.REMOVE, s("/e/d")), sftp.FAILURE)
        self.assertEqual(client.status(sftp.REMOVE, s("/e/d/a.txt")), sftp.OK)
        self.assertEqual(client.status(sftp.RMDIR, s("/e/d")), sftp.OK)
        client.end()
        self.assertEqual(self.listed(), {})
        self.assertEqual(sorted(p.name for p in paths.STORE_DIR.iterdir()), ["e"])
        commands = [c for c, _o, _v in self.audit()]
        self.assertEqual(commands.count("store.move"), 1)
        self.assertEqual(commands.count("store.delete"), 2)

    def test_free_space_is_what_the_store_has_left(self):
        self.config = dataclasses.replace(self.config, store_max_gb=1)
        self.put("a.bin", b"x" * 1024)
        client = self.session(role="viewer")
        self.assertIn(sftp.STATVFS.encode(), client.extensions)
        reply = client.call(sftp.EXTENDED, s(sftp.STATVFS) + s("/"))
        self.assertEqual(reply.kind, sftp.EXTENDED_REPLY)
        _bsize, unit, _blocks, free, avail = (reply.u64() for _ in range(5))
        self.assertLessEqual(avail * unit, 1024 ** 3 - 1024)
        self.assertEqual(free, avail)
        for _ in range(4):
            reply.u64()
        self.assertEqual(reply.u64(), 1)  # read-only for a viewer

    def test_what_is_not_sftp_ends_the_session(self):
        client = self.session()
        client.to.write(u32(10 * 1024 * 1024))
        client.to.flush()
        answer = client.end()
        self.assertEqual(answer["error"]["code"], "bad_request")

    def test_only_from_a_client(self):
        self.assertEqual(self.code("store.sftp", role="viewer"), "bad_request")
