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

## Next

- [ ] Confirm `docker run hello-world` without sudo
- [x] Install Ollama; `ollama run granite4:3b`; check `ollama ps` shows GPU
- [ ] NVIDIA Container Toolkit
- [ ] Open WebUI
- [ ] GNS3 lab with two Cisco routers (OSPF) for Phase 3
- [ ] Start Claude Code with `docs/PROJECT_PROMPT.md`
