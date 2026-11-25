from rootvex.redact import SECRET, Redactor

FORTIGATE = """\
config system global
    set hostname "FW-HQ-01"
end
config system interface
    edit "port1"
        set ip 172.16.10.1 255.255.255.0
    next
end
config vpn ipsec phase1-interface
    edit "to-branch"
        set remote-gw 203.0.113.10
        set psksecret ENC dGhpc2lzYXZlcnlsb25nc2VjcmV0dmFsdWU=
    next
end
config system admin
    edit "admin"
        set password ENC SH2abcdefghijklmnopqrstuvwxyz0123456789
    next
end
config system snmp community
    edit 1
        set name "s3cr3tcomm"
    next
end
config router ospf
    set authentication md5
    set router-id 172.16.0.1
end
config firewall address
    edit "erp"
        set fqdn "erp.corp-internal.net"
    next
end
"""

CISCO = """\
hostname CORE-SW-01
enable secret 9 $9$abcdEFGHijkl$mnopQRSTuvwxyz
username admin privilege 15 secret 9 $9$zzzzYYYYxxxx
snmp-server community Publ1cRW RW
crypto isakmp key MyPsk123 address 198.51.100.7
interface Vlan10
 ip address 192.168.10.1 255.255.255.0
 ip ospf message-digest-key 1 md5 OspfKey!
tacacs server ISE
 key 7 0822455D0A16
mac address-table static aabb.ccdd.eeff vlan 10 interface Gi1/0/1
access-list 101 permit ip 10.20.0.0 0.0.255.255 any
"""

HUAWEI = """\
sysname AGG-HW-02
local-user admin password irreversible-cipher $1c$abc$def
snmp-agent community read cipher %^%#xyz%^%#
ike peer branch
 pre-shared-key cipher %@%@qwerty%@%@
"""

PALO_XML = "<users><entry name='admin'><phash>$1$abc$xyz</phash></entry></users>" \
           "<hostname>PA-EDGE-1</hostname>"


def redact(text, **kw):
    r = Redactor(**kw)
    return r, r.redact(text)


def test_fortigate_secrets_and_addresses():
    r, out = redact(FORTIGATE)
    for leaked in ("dGhpc2lz", "SH2abc", "s3cr3tcomm", "FW-HQ-01", "172.16.10.1",
                   "203.0.113.10", "corp-internal"):
        assert leaked not in out, leaked
    assert "255.255.255.0" in out                      # netmask kept
    assert "set authentication md5\n" in out           # did not eat the next line
    assert out.count(SECRET) == 3
    assert "HOST-1" in out


def test_private_subnet_structure_kept():
    r, out = redact("gw 172.16.10.1 host 172.16.10.20 other 172.16.99.5 lan 192.168.1.5")
    a, b, c, d = out.split()[1::2]
    assert a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]  # same /24 stays same /24
    assert a.split(".")[:2] == c.split(".")[:2]        # same /16 stays same /16
    assert d.split(".")[:2] != a.split(".")[:2]
    assert all(x.startswith("10.") for x in (a, b, c, d))


def test_cisco():
    r, out = redact(CISCO)
    for leaked in ("$9$abcd", "$9$zzzz", "Publ1cRW", "MyPsk123", "OspfKey!", "0822455D0A16",
                   "CORE-SW-01", "192.168.10.1", "198.51.100.7", "aabb.ccdd.eeff"):
        assert leaked not in out, leaked
    assert "0.0.255.255" in out                        # wildcard kept
    assert "0200.0000.0001" in out                     # dotted MAC stays dotted
    assert "host-" not in out                          # MAC not mistaken for a domain


def test_huawei_and_palo_alto():
    _, out = redact(HUAWEI)
    for leaked in ("$1c$abc", "%^%#xyz", "qwerty", "AGG-HW-02"):
        assert leaked not in out, leaked
    _, out = redact(PALO_XML)
    assert "$1$abc" not in out and "PA-EDGE-1" not in out


def test_round_trip_restores_everything_but_secrets():
    r, out = redact(CISCO + FORTIGATE)
    back = r.restore(out)
    assert "172.16.10.1" in back and "CORE-SW-01" in back and "aabb.ccdd.eeff" in back
    assert "erp.corp-internal.net" in back
    assert "MyPsk123" not in back                      # secrets never come back


def test_restore_new_address_in_mapped_subnet():
    r, out = redact("server 172.16.10.20")
    fake_prefix = out.split()[1].rsplit(".", 1)[0]
    model_answer = f"add a route via {fake_prefix}.254"
    assert r.restore(model_answer) == "add a route via 172.16.10.254"


def test_extra_terms_and_emails():
    text = "Contoso HQ outage reported by ahmed@contoso.com"
    r, out = redact(text, extra_terms=["Contoso"])
    assert "Contoso" not in out and "ahmed" not in out and "contoso.com" not in out
    assert r.restore(out) == text


def test_ipv6_and_logs():
    r, out = redact('devname="FW-HQ-01" srcip=2a02:1234:5678:1::10 time=10:30:45 '
                    'dstip=8.8.8.8 file=config.txt api_key=sk-12345678')
    assert "2a02:1234" not in out and "FW-HQ-01" not in out and "sk-1234" not in out
    assert "time=10:30:45" in out and "config.txt" in out
    assert r.restore(out).startswith('devname="FW-HQ-01" srcip=2a02:1234:5678:1::10')


def test_public_vendor_domains_kept():
    _, out = redact("see docs.fortinet.com and update.fortiguard.com")
    assert "docs.fortinet.com" in out and "update.fortiguard.com" in out


def test_known_secrets_scrubbed_everywhere():
    r, out = redact("login with LabPass123 then LabPass123 again", known_secrets={"LabPass123"})
    assert "LabPass123" not in out and out.count(SECRET) == 2
