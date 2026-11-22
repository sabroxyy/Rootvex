# Workstation setup log

Ubuntu 26.04.1 LTS on Dell Precision 7810 (BIOS A19, UEFI, Secure Boot off).
User `sabroxyy`, hostname `Sabroxyy`.

## 1. Bootable USB

- Rufus: Ubuntu ISO, **GPT**, **UEFI (non CSM)**, **FAT32**, ISO Image mode.
- Earlier attempt failed because the USB was NTFS/MBR — the UEFI firmware
  ignored it. Boot menu on Dell: **F12**.

## 2. Install

- Interactive install → default selection → third-party graphics drivers.
- Disk: **Erase disk** on `sda` (256 GB SSD). Kept `sdb` (1 TB HDD) untouched.
- No encryption.

## 3. Network troubleshooting

Symptom: Ethernet linked at 100 Mb/s but DHCP never got a lease; a static IP
couldn't reach the gateway (`Destination Host Unreachable`).

Root cause: the TP-Link router had **Interface Grouping** — LAN1/LAN2 were in a
separate group with no WAN, isolated from the 192.168.1.0/24 DHCP network.

Fix: move the cable to **LAN3 or LAN4** (or delete the extra group).

Other findings:
- USB Wi-Fi adapter `wlxb0192115d202` sees only 2.4 GHz networks.
- Router DHCP pool: 192.168.1.100–199, gateway 192.168.1.1.

Useful commands:
```bash
cat /sys/class/net/enp0s25/speed
nmcli device status
journalctl -b -u NetworkManager | grep -i dhcp | tail -20
ip a; ip route; ip neigh
```

## 4. NVIDIA driver

Installed during setup; verified with `nvidia-smi`:
GTX 1060 6GB, driver 580.178.04, CUDA 13.0.

## 5. Docker

Old guides use `apt-key`, which no longer exists. Working method:
```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG ubridge,libvirt,kvm,wireshark,docker $USER   # then log out/in
docker run hello-world
```

## 6. GitHub

- `git` + `gh` from Ubuntu repos; SSH key `~/.ssh/id_ed25519` added to GitHub
  as `Sabroxyy-Ubuntu`.
- `gh` 2.46 has no `--skip-ssh-key`; use:
  `gh auth login --hostname github.com --git-protocol ssh --web`

## 7. Keyboard backlight (Voxme, Scroll Lock LED)

GNOME resets the Scroll Lock LED on lock/wake, so a small service keeps it on:
```bash
# /usr/local/bin/kbd-light.sh — loop setting /sys/class/leds/*scrolllock/brightness to 1 every 2 s
# /etc/systemd/system/kbd-light.service — Restart=always, WantedBy=multi-user.target
sudo systemctl enable --now kbd-light.service
```

## 8. Claude desktop app

Crashed once before the NVIDIA driver was active; hardware acceleration was
turned off automatically. Re-enable from the Help menu now that the driver works.

## 9. Ollama + GPU check (2026-11-21)

- Ollama binary had no systemd unit; fixed by rerunning the official installer
  (`curl -fsSL https://ollama.com/install.sh | sh`) -> 0.40.17, `ollama.service` enabled.
- `bash lab/check_gpu.sh --load` results:
  - GTX 1060 6GB, PCIe x16, driver 580.178.04, CUDA 13.0, no Xid errors.
  - `granite4:3b`: **100% GPU**, **42.3 tokens/s**, 3.4 GB VRAM, peak 59 C / 122 W, 98% utilization.
- Ollama skips its `cuda_v13` libraries for this card (compute capability 6.1 is
  not compiled in) and uses `cuda_v12` instead. **Pascal support depends on
  Ollama still shipping cuda_v12**: before upgrading Ollama, check the release
  notes, and if a new version drops the GPU, reinstall the last good one with
  `curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION=0.40.17 sh`.

## 10. First real agent runs (fixtures, OSPF area mismatch)

| Model | Result |
|---|---|
| `granite4:3b`, first run | Missed the cause: saw "no neighbors" and stopped (several commands had no recording) |
| `granite4:3b`, more recordings | **Correct**, high confidence: R1 area 0 vs R2 area 1, 11 steps, evidence verified |
| `qwen3.5:4b` | Timed out: 3,000+ "thinking" tokens in one step at ~29 tok/s. Fixed with `max_tokens` and `reasoning_effort` settings |

## 11. GNS3 lab: c7200 image does not boot (2026-11-22)

- GNS3 2.2.61 + KVM + ubridge + Docker all healthy (`lab/check_gns3_env.sh`); GNS3 VM off.
- Only router image available: `c7200-advipservicesk9-mz.152-4.S5.image` (ELF, 84 MB,
  md5 f90e916405f5da26b708ed2e9095351e). Lab built with `lab/build_gns3_lab.py`.
- Every c7200 crashes ~0.2 s after power-on: `TLB (Store) Exception`, PC 0x8316ede0,
  write to 0x38005f, looping. Dynamips 0.2.25-1~resolute1.
- Ruled out with `lab/test_c7200.py`: RAM 256/384/512, sparse memory on/off, midplane
  std/vxr, I/O memory 10%, ghost IOS off, Idle-PC empty.
- **Incident:** the crash loop wrote ~105 GB of logs (`dynamips_i*_stdout.txt`,
  `c7200_i*_log.txt`) and filled the SSD. Deleted; test scripts now stop a router after
  3 crash messages. Never leave crashing Dynamips routers running.
- Conclusion: this image does not run on this Dynamips build. Need another router image.

## 12. GNS3 environment check (2026-11-22)

`bash lab/check_gns3_env.sh`: KVM usable, user in ubridge/libvirt/kvm/wireshark/docker,
GNS3 GUI and server 2.2.61, ubridge with cap_net_admin, dynamips 0.2.25, QEMU 10.2.1,
mtools, Docker without sudo, GNS3 VM disabled, server on 127.0.0.1:3080 with auth.
`tap-nw` created with nmcli (10.99.0.1/24). VirtualBox 7.2 also installed (unused).

## 13. Rename to Rootvex and clean-up (2026-11-22/23)

- Merged the 16 newer commits from `~/netwarden` into the renamed repo
  (`~/projects/rootvex`, `github.com/sabroxyy/Rootvex`), renamed them, and
  renamed what had been kept for the old lab (device login `rootvex`, GNS3
  `rootvex-lab`, templates `Rootvex …`, `RV-*` config names).
- `.env` copied from `~/netwarden` with `NETWARDEN_*` → `ROOTVEX_*`.
- `uv run python lab/build_gns3_lab.py --remove-old` removed the old projects and
  templates, after `sudo rm -rf` of root-owned Docker files in the old project.
- Deleted `~/netwarden`, `~/projects/netwarden`, `~/Desktop/NetWarden` and the
  old branches; GitHub has only `main`.
- `bash scripts/check_old_name.sh`: "No old names left that need action".
  Kept on purpose: old Claude Code chat history under `~/.claude/projects/`.

## 14. Live lab up, Phase 3 complete (2026-11-22/23)

- Image `c7200-adventerprisek9-mz.124-24.T5.image` (98 MB, md5
  `6b89d0d804e1f2bb5b8bda66b5692047` matches) in `~/GNS3/images/IOS/`.
  `lab/test_c7200.py`: BOOTS with default settings.
- `uv run python lab/build_gns3_lab.py --rebuild --start`: project `rootvex-lab`,
  Idle-PC set (routers ~7% CPU each), 0 crash messages.
- SSH: IOS 12.4 did not create its RSA key from the startup config → keys now
  created on the consoles (`--ssh`). OpenSSH refused IOS 12.4's SHA-1 key exchange
  → `~/.ssh/config` entry for `10.99.0.*`. paramiko 5 had removed those
  algorithms → pinned paramiko 4.0.0. Rebuilds change host keys → lab-only
  `StrictHostKeyChecking no` / `UserKnownHostsFile /dev/null`.
- `.env` values were not reaching the SSH runner → exported at start-up.
- Results: `ssh rootvex@10.99.0.11` → `SSH Enabled - version 2.0`;
  `rootvex ask "Is OSPF healthy between R1 and R2?"` → real output, OSPF healthy
  (R1–R2 FULL, R2–BR1 FULL over Tunnel0); `configure terminal`, `conf t`,
  `reload` → DENIED and logged.

## Next

- [x] Confirm `docker run hello-world` without sudo
- [x] Install Ollama; `ollama run granite4:3b`; check `ollama ps` shows GPU
- [x] GNS3 environment healthy (`lab/check_gns3_env.sh`)
- [x] Management interface `tap-nw`
- [x] Copy `.env` from `~/netwarden` to `~/projects/rootvex`, `NETWARDEN_*` renamed to `ROOTVEX_*`
      (`config/inventory.local.yaml` never existed there; create it from the example)
- [x] Check the workstation for the old name: `bash scripts/check_old_name.sh`
- [x] Remove the old GNS3 project and templates: `uv run python lab/build_gns3_lab.py --remove-old`
- [x] Retire `~/netwarden`
- [x] Router image: c7200 IOS 12.4(24)T5 in `~/GNS3/images/IOS/`
- [x] `cp config/inventory.example.yaml config/inventory.local.yaml`
- [x] Build the lab: `uv run python lab/build_gns3_lab.py --rebuild --start`
- [x] `ssh rootvex@10.99.0.11` from the host; Phase 3 live-lab checks passed
- [ ] Optional: IOSv + IOSvL2 (CML-Personal) for a managed SW1
- [ ] NVIDIA Container Toolkit (Phase 9)
- [ ] Open WebUI (Phase 7, optional)
