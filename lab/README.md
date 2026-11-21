# NetWarden lab (GNS3)

Stage B of the lab in [`docs/DESIGN.md`](../docs/DESIGN.md) section 9, as code.
Interactive picture: [`docs/lab-topology-3d.html`](../docs/lab-topology-3d.html).

| File | What it is |
|---|---|
| `topology.yaml` | Nodes, templates and links. The one place to change the lab |
| `configs/*.cfg` | Full IOS startup configs for R1, R2, ISP1, BR1 (IOSv) and SW1 (IOSvL2) |
| `hosts/*.interfaces` | Network settings for PC1, PC2 and WEB1 (Docker) |
| `build_gns3_lab.py` | Builds the whole project through the GNS3 API |

## GNS3 or PNETLab?

Use **GNS3** for this project. It is already installed on the workstation
(2.2.61 with KVM), runs natively on Ubuntu, and the build script drives it
through its API. PNETLab is a separate Ubuntu-based VM: running it on the
15 GiB machine means nested virtualization and a few GB of RAM lost to the VM
itself before any router starts. The configs in `configs/` are plain IOS and
work in PNETLab too (see the end of this page).

## Build it in GNS3

One-time host setup:

```bash
sudo apt install -y mtools        # GNS3 needs it to write startup configs onto the IOSv config disk
```

```bash
nmcli connection add type tun ifname tap-nw con-name tap-nw mode tap owner $(id -u) \
  ipv4.method manual ipv4.addresses 10.99.0.1/24 ipv6.method disabled
```

```bash
nmcli connection up tap-nw
```

1. Copy the Cisco images (CML-Personal) to `~/GNS3/images/QEMU/`. If your file
   names differ from the defaults, set `LAB_IOSV_IMAGE` and `LAB_IOSVL2_IMAGE` in `.env`.
2. In `.env`, set `LAB_DEVICE_PASSWORD` and `LAB_VPN_PSK` (lab-only values).
3. Open the GNS3 GUI (it starts the local server), then from the repo root:

```bash
uv run python lab/build_gns3_lab.py --start
```

The script reads the GUI's local-server login from
`~/.config/GNS3/*/gns3_server.conf`. It creates four templates
(`NetWarden IOSv`, `NetWarden IOSvL2`, `NetWarden ipterm`, `NetWarden nginx`),
the `netwarden-lab` project, 10 nodes and 13 links, and loads every config.
Run it again with `--rebuild` to start from scratch. IOSv only reads the
config disk on its first boot, so use `--rebuild` after editing `configs/`.

RAM: about 3.5 GB with everything running.

## Check it works

Give the routers 2 to 4 minutes to boot, then:

```bash
ssh netwarden@10.99.0.11        # R1 (also .12 R2, .13 BR1, .14 ISP1, .21 SW1)
```

On the routers:

| Command | Expect |
|---|---|
| `show ip ospf neighbor` (R1) | R2 (2.2.2.2) in FULL |
| `show ip ospf neighbor` (R2) | R1 on Gi0/1 and BR1 (3.3.3.3) on Tunnel0 |
| `show ip bgp summary` (ISP1) | Both neighbors up, prefixes received |
| `show crypto ipsec sa` (R2) | Packets encrypted/decrypted increasing |
| `show ip nat translations` (R2) | Entries after PC1 browses |

From PC1 (GNS3 console): `ping 10.20.20.10` goes over the tunnel, and
`curl http://www.lab.test` returns the nginx page through NAT.

Then point NetWarden at it:

```bash
cp config/inventory.example.yaml config/inventory.local.yaml   # all five lab devices
```

```bash
uv run netwarden ask "Is OSPF healthy between R1 and R2?" --steps
```

## Design notes

- Management sits in VRF `MGMT` on Gi0/0 (SVI VLAN 99 on SW1). Breaking OSPF,
  BGP or the VPN never cuts the agent's SSH.
- VTY lines accept SSH only, and only from 10.99.0.1 (`access-class NW-MGMT`).
- R1 uses a static default to R2. R2 and BR1 learn `192.0.2.0/24` (web, DNS)
  and each other's WAN /30 through eBGP with ISP1. The tunnel therefore
  depends on BGP, which makes "ISP link down" a realistic multi-symptom fault.
- HQ and Branch LANs reach each other with OSPF over the IPsec VTI. Internet
  traffic is PAT-ed on R2 Gi0/2 and BR1 Gi0/1.
- ISP1 runs DNS: `www.lab.test` resolves to 192.0.2.80.

## Using PNETLab instead

PNETLab (or EVE-NG) can run the same lab by hand:

1. Upload the images as `vios-*` / `viosl2-*` QEMU folders (`virtioa.qcow2`).
2. Recreate the nodes and links from `topology.yaml`, with management on a
   Cloud/pnet bridge for 10.99.0.0/24.
3. Replace the double-brace secrets in `configs/*.cfg` with your values and paste
   each config into the node's console, or upload it as the node's startup config.
4. Point NetWarden's inventory at the management IPs; nothing else changes.

The build script only targets the GNS3 API.
