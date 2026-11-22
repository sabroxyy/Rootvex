"""ensure_ssh against a fake IOS console (no GNS3 or router needed)."""

import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))
from ios_console import ConsoleError, ensure_ssh  # noqa: E402


class FakeIOS:
    """Tiny IOS console: user EXEC -> enable -> config -> crypto key -> write mem."""

    def __init__(self, hostname="R1", secret="Lab-pass", has_key=False, booting_lines=2):
        self.hostname, self.secret, self.has_key = hostname, secret, has_key
        self.booting_lines = booting_lines
        self.commands: list[str] = []
        self.saved = False
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        conn.sendall(b"\xff\xfb\x01\xff\xfb\x03Connected to Dynamips VM\r\n")
        mode, waiting_pw = ">", False
        buf = b""
        while True:
            data = conn.recv(1024)
            if not data:
                return
            buf += data
            while b"\r" in buf:
                line, buf = buf.split(b"\r", 1)
                cmd = line.decode().strip()
                self.commands.append("<secret>" if waiting_pw else cmd)
                out = ""
                if self.booting_lines > 0:  # still booting: ignore input
                    self.booting_lines -= 1
                    conn.sendall(b"%SYS-5-RESTART: System restarted\r\n")
                    continue
                if waiting_pw:
                    waiting_pw = False
                    if cmd == self.secret:
                        mode = "#"
                    else:
                        out = "% Access denied\r\n"
                elif cmd == "enable":
                    conn.sendall(b"Password: ")
                    waiting_pw = True
                    continue
                elif cmd == "show ip ssh":
                    state = "Enabled" if self.has_key else "Disabled"
                    out = f"SSH {state} - version 2.0\r\n"
                elif cmd == "configure terminal":
                    mode = "(config)#"
                elif cmd.startswith("crypto key generate rsa"):
                    self.has_key = True
                    out = "% Generating 2048 bit RSA keys, keys will be non-exportable...[OK]\r\n"
                elif cmd == "end":
                    mode = "#"
                elif cmd == "write memory":
                    self.saved = True
                    out = "Building configuration...\r\n[OK]\r\n"
                conn.sendall(f"{out}\r\n{self.hostname}{mode}".encode())


def test_creates_and_saves_key_when_ssh_disabled():
    ios = FakeIOS()
    assert ensure_ssh(ios.port, "R1", "Lab-pass", boot_timeout=60, poll=1) == "key created"
    assert ios.has_key and ios.saved
    assert "crypto key generate rsa general-keys modulus 2048" in ios.commands
    assert "Lab-pass" not in ios.commands  # password sent, but our fake records it masked


def test_leaves_router_alone_when_ssh_already_enabled():
    ios = FakeIOS(has_key=True, booting_lines=0)
    assert ensure_ssh(ios.port, "R1", "Lab-pass") == "already enabled"
    assert not any(c.startswith("crypto") for c in ios.commands) and not ios.saved


def test_wrong_enable_password_is_reported():
    ios = FakeIOS(booting_lines=0)
    with pytest.raises(ConsoleError, match="enable password rejected"):
        ensure_ssh(ios.port, "R1", "wrong")
