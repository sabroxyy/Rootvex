# Rootvex lab (GNS3)

Stage B of the lab in [`docs/DESIGN.md`](../docs/DESIGN.md) section 9, as code.
Interactive picture: [`docs/rootvex-lab-3d.html`](../docs/rootvex-lab-3d.html).

| File | What it is |
|---|---|
| `topology.yaml` | Nodes, templates and links. The one place to change the lab |
| `configs/*.cfg` | Full IOS startup configs for R1, R2, ISP1, BR1, written with logical ports (`{{IF1}}`...). `SW1.cfg` is for a future IOSvL2 switch |
| `hosts/*.interfaces` | Network settings for PC1, PC2 and WEB1 (Docker) |
| `build_gns3_lab.py` | Builds the whole project through the GNS3 API |

## GNS3 or PNETLab?

Use **GNS3** for this project. It is already installed on the workstation
(2.2.61 with KVM), runs natively on Ubuntu, and the build script drives it
through its API. PNETLab is a separate Ubuntu-based VM: running it on the
15 GiB machine means nested virtualization and a few GB of RAM lost to the VM
itself before any router starts. The configs in `configs/` are plain IOS and
work in PNETLab too (see the end of this page).

## Router platform

Routers run as **Cisco 7200 (Dynamips)** with **`c7200-adventerprisek9-mz.124-24.T5.image`**
(IOS 12.4(24)T5: OSPF, BGP, NAT, IPsec; md5 `6b89d0d804e1f2bb5b8bda66b5692047`).
This is the working lab image. No licence key is needed in GNS3.

```
LAB_ROUTER_PLATFORM=c7200
LAB_C7200_IMAGE=c7200-adventerprisek9-mz.124-24.T5.image
```

| Logical port | c7200 | IOSv |
|---|---|---|
| `mgmt` (VRF MGMT) | FastEthernet0/0 | GigabitEthernet0/0 |
| `if1` | GigabitEthernet1/0 | GigabitEthernet0/1 |
| `if2` | GigabitEthernet2/0 | GigabitEthernet0/2 |
| `if3` | GigabitEthernet3/0 | GigabitEthernet0/3 |

The management VRF uses `ip vrf` syntax on this platform (IOS 12.4 has no
`vrf definition`). The older `c7200-advipservicesk9-mz.152-4.S5` image does
**not** boot on Dynamips 0.2.25 (TLB exception loop, `docs/SETUP_LOG.md` section 11).

**Optional: IOSv + IOSvL2** from CML-Personal give a managed SW1:

```
LAB_ROUTER_PLATFORM=iosv
LAB_SWITCH_PLATFORM=iosvl2
```

The configs don't change. With `iosvl2`, SW1 is a managed switch (10.99.0.21)
with ports p1 = Gi1/0 (PC1), p2 = Gi0/1 (R1) and mgmt = Gi0/0; with `builtin`,
SW1 is GNS3's unmanaged switch and its management link is skipped.

Debug helpers: `lab/test_c7200.py` (Dynamips variants in a throwaway project)
and `lab/test_dynamips_direct.sh` (Dynamips without GNS3). Both stop a router
after the first crash messages, because a crash loop can fill the disk.

## Build it in GNS3

First check the workstation (read-only, no sudo) and fix any FAIL lines:

```bash
bash lab/check_gns3_env.sh | tee gns3-check.txt
```

One-time host setup:

```bash
sudo apt install -y mtools        # only needed for IOSv (config disk)
```

```bash
nmcli connection add type tun ifname tap-nw con-name tap-nw mode tap owner $(id -u) \
  ipv4.method manual ipv4.addresses 10.99.0.1/24 ipv6.method disabled
```

```bash
nmcli connection up tap-nw
```

1. Copy `c7200-advipservicesk9-mz.152-4.S5.image` to `~/GNS3/images/IOS/`
   (or set `LAB_C7200_IMAGE` in `.env` to your file name).
2. In `.env`, set `LAB_DEVICE_PASSWORD` and `LAB_VPN_PSK` (lab-only values).
3. Open the GNS3 GUI (it starts the local server), then from the repo root:

```bash
uv run python lab/build_gns3_lab.py --start
```

The script reads the GUI's local-server login from
`~/.config/GNS3/*/gns3_server.conf`. It creates three templates
(`Rootvex c7200`, `Rootvex ipterm`, `Rootvex nginx`), the `rootvex-lab`
project, 11 nodes and 12 links, and loads every config. With `--start` it also
computes an **Idle-PC** value for the 7200s; without one, each router uses a
full CPU core. Run it again with `--rebuild` to start from scratch after
editing `configs/`.

Objects from before the rename (project `netwarden-lab`, templates `NetWarden …`)
are removed with `uv run python lab/build_gns3_lab.py --remove-old`.

RAM: about 2.5 GB with everything running (4 x 512 MB routers + containers).

## SSH to classic IOS from your PC

IOS 12.4 and IOSv 15.x only offer SHA-1 key exchange and `ssh-rsa` host keys,
which current OpenSSH refuses by default ("no matching key exchange method").
Allow them for the lab network only, in `~/.ssh/config`:

```
Host 10.99.0.*
    KexAlgorithms +diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1
    HostKeyAlgorithms +ssh-rsa
    PubkeyAcceptedAlgorithms +ssh-rsa
    Ciphers +aes128-cbc,aes256-cbc
```

Rootvex itself connects through paramiko, pinned to 4.0.0 for the same reason
(paramiko 5 removed these algorithms).

IOS 12.4 also does not create its RSA key from the startup config, so SSH starts
disabled. `--start` fixes this on every router through its console
(`crypto key generate rsa`, then `write memory`); on a running lab use
`uv run python lab/build_gns3_lab.py --ssh`.

## Check it works

Give the routers 1 to 3 minutes to boot, then:

```bash
ssh rootvex@10.99.0.11        # R1 (also .12 R2, .13 BR1, .14 ISP1)
```

On the routers:

| Command | Expect |
|---|---|
| `show ip ospf neighbor` (R1) | R2 (2.2.2.2) in FULL |
| `show ip ospf neighbor` (R2) | R1 on Gi1/0 and BR1 (3.3.3.3) on Tunnel0 |
| `show ip bgp summary` (ISP1) | Both neighbors up, prefixes received |
| `show crypto ipsec sa` (R2) | Packets encrypted/decrypted increasing |
| `show ip nat translations` (R2) | Entries after PC1 browses |

From PC1 (GNS3 console): `ping 10.20.20.10` goes over the tunnel, and
`curl http://www.lab.test` returns the nginx page through NAT.

Then point Rootvex at it:

```bash
cp config/inventory.example.yaml config/inventory.local.yaml   # the four lab routers
```

```bash
uv run rootvex ask "Is OSPF healthy between R1 and R2?" --steps
```

## Design notes

- Management sits in VRF `MGMT` on the `mgmt` port (Fa0/0 on the 7200). Breaking OSPF,
  BGP or the VPN never cuts the agent's SSH.
- VTY lines accept SSH only, and only from 10.99.0.1 (`access-class RV-MGMT`).
- R1 uses a static default to R2. R2 and BR1 learn `192.0.2.0/24` (web, DNS)
  and each other's WAN /30 through eBGP with ISP1. The tunnel therefore
  depends on BGP, which makes "ISP link down" a realistic multi-symptom fault.
- HQ and Branch LANs reach each other with OSPF over the IPsec VTI. Internet
  traffic is PAT-ed on R2 `if2` and BR1 `if1`.
- ISP1 runs DNS: `www.lab.test` resolves to 192.0.2.80.

## Using PNETLab instead

PNETLab (or EVE-NG) can run the same lab by hand:

1. Upload the router image (c7200 under Dynamips, or IOSv as a `vios-*` QEMU folder).
2. Recreate the nodes and links from `topology.yaml`, with management on a
   Cloud/pnet bridge for 10.99.0.0/24.
3. Replace the double-brace values in `configs/*.cfg` (secrets and the `IF` interface
   names from the table above) with your values and paste
   each config into the node's console, or upload it as the node's startup config.
4. Point Rootvex's inventory at the management IPs; nothing else changes.

The build script only targets the GNS3 API.
