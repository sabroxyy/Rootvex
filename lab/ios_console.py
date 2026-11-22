"""Talk to a Cisco IOS router over its GNS3 telnet console.

Used only by the lab build to finish set-up that IOS will not do from a startup
config: IOS 12.4 does not run `crypto key generate rsa` at boot, so SSH stays
disabled until a key exists. This is lab plumbing, not an agent tool: the agent
never gets console access and still cannot change devices.
"""

from __future__ import annotations

import re
import socket
import time

_TELNET_CMD = re.compile(rb"\xff[\xfb-\xfe].|\xff[\xf0-\xfa]", re.S)


class ConsoleError(RuntimeError):
    pass


class IOSConsole:
    def __init__(self, port: int, hostname: str, host: str = "127.0.0.1", timeout: float = 10):
        self.hostname = hostname
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(0.5)
        self.buf = ""

    def close(self) -> None:
        self.sock.close()

    def send(self, line: str) -> None:
        self.buf = ""
        self.sock.sendall(line.encode() + b"\r")

    def expect(self, patterns: list[str], timeout: float) -> int:
        """Read until one of the regexes matches the output; return its index."""
        compiled = [re.compile(p) for p in patterns]
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for i, rx in enumerate(compiled):
                if rx.search(self.buf):
                    return i
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                raise ConsoleError("console closed the connection")
            self.buf += _TELNET_CMD.sub(b"", chunk).decode(errors="ignore")
        tail = self.buf[-200:].replace("\r", "")
        raise ConsoleError(f"timeout waiting for {patterns} (last output: {tail!r})")

    def run(self, command: str, mode: str = "#", timeout: float = 30) -> str:
        """Send a command and return its output, waiting for its own echo then the prompt.

        Waiting for the echo first stops a prompt left over from the previous
        command from being taken as this command's end.
        """
        self.send(command)
        echo_then_prompt = re.escape(command) + r"[\s\S]*?" + self.prompt(mode)
        self.expect([echo_then_prompt], timeout)
        return self.buf

    def prompt(self, mode: str = "#") -> str:
        return rf"{re.escape(self.hostname)}{re.escape(mode)}\s*$"


def ensure_ssh(
    port: int, hostname: str, enable_password: str, boot_timeout: float = 600, poll: float = 15
) -> str:
    """Make sure SSH is enabled on the router; create and save an RSA key if needed.

    Returns "already enabled" or "key created". Raises ConsoleError on failure.
    """
    con = IOSConsole(port, hostname)
    try:
        # Wake the console; IOS may still be booting.
        exec_p, priv_p = con.prompt(">"), con.prompt("#")
        deadline = time.monotonic() + boot_timeout
        while True:
            con.send("")
            try:
                i = con.expect(
                    [priv_p, exec_p, r"Press RETURN", r"initial configuration dialog"], poll
                )
            except ConsoleError:
                if time.monotonic() > deadline:
                    raise
                continue
            if i == 3:
                raise ConsoleError(f"{hostname} has no startup config (setup dialog shown)")
            if i in (0, 1):
                break
        if i == 1:
            con.send("enable")
            if con.expect([r"Password:", priv_p], 20) == 0:
                con.send(enable_password)
                if con.expect([priv_p, r"Password:", r"% (Access denied|Bad secrets)"], 20):
                    raise ConsoleError(f"{hostname}: enable password rejected")
        con.run("terminal length 0", timeout=20)
        if "SSH Enabled" in con.run("show ip ssh"):
            return "already enabled"
        con.send("configure terminal")
        con.expect([con.prompt("(config)#")], 20)
        con.send("crypto key generate rsa general-keys modulus 2048")
        created = True
        if con.expect([con.prompt("(config)#"), r"replace them\? \[yes/no\]"], 600) == 1:
            created = False  # a key already existed; keep it
            con.send("no")
            con.expect([con.prompt("(config)#")], 20)
        con.send("end")
        con.expect([priv_p], 20)
        con.send("write memory")
        con.expect([r"\[OK\]", priv_p], 120)
        con.expect([priv_p], 60)
        if "SSH Enabled" not in con.run("show ip ssh"):
            raise ConsoleError(f"{hostname}: key generated but SSH is still disabled")
        return "key created" if created else "already enabled (key kept)"
    finally:
        con.close()
