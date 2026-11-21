#!/usr/bin/env bash
# Read-only health check of GNS3, the GNS3 VM and everything the NetWarden lab needs.
# Changes nothing, needs no sudo. Passwords in config files are masked.
#
#   bash lab/check_gns3_env.sh | tee gns3-check.txt
#
# Each line starts with OK, WARN or FAIL, then the detail. Paste the output back to Claude.

set -u
ok()   { printf 'OK    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; }
info() { printf '      %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
lsmod() { cat /proc/modules 2>/dev/null; }   # same first column as lsmod, never missing
have ip || ip() { :; }
mask() { sed -E 's/((password|secret|token)[^=:]*[=:][[:space:]]*"?)[^",]+/\1***/I'; }

LAB_IOSV=vios-adventerprisek9-m.spa.159-3.m9.qcow2
LAB_IOSVL2=vios_l2-adventerprisek9-m.ssa.high_iron_20200929.qcow2
if [ -f .env ]; then
  v=$(grep -E '^LAB_IOSV_IMAGE=' .env | cut -d= -f2-);   [ -n "$v" ] && LAB_IOSV=$v
  v=$(grep -E '^LAB_IOSVL2_IMAGE=' .env | cut -d= -f2-); [ -n "$v" ] && LAB_IOSVL2=$v
fi

section "Host"
info "$(. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME") | kernel $(uname -r) | user $(id -un)"
mem_total=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
mem_avail=$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo)
info "RAM ${mem_total} GiB total, ${mem_avail} GiB available"
awk '/MemAvailable/ {exit !($2 < 4*1048576)}' /proc/meminfo && warn "Less than 4 GiB free: the lab needs about 3.5 GB"
df -h "$HOME" | awk 'NR==2 {print "      Disk for $HOME: " $4 " free of " $2}'

section "CPU virtualization / KVM"
if grep -qE 'vmx|svm' /proc/cpuinfo; then ok "CPU has VT-x/AMD-V"; else fail "No vmx/svm flag: enable virtualization in the BIOS"; fi
if [ -e /dev/kvm ]; then
  if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then ok "/dev/kvm is usable by $(id -un)"; else fail "/dev/kvm exists but $(id -un) cannot use it (add to group kvm, log out/in)"; fi
else
  fail "/dev/kvm missing (kvm_intel module not loaded?)"
fi
lsmod | grep -qE '^kvm_(intel|amd)' && ok "KVM module loaded: $(lsmod | grep -oE '^kvm_(intel|amd)' | head -1)"
nested=$(cat /sys/module/kvm_intel/parameters/nested 2>/dev/null || cat /sys/module/kvm_amd/parameters/nested 2>/dev/null)
info "Nested virtualization: ${nested:-unknown} (only matters if you run the GNS3 VM)"

section "Groups (need a log out/in after changes)"
for g in ubridge libvirt kvm wireshark docker; do
  if id -nG | tr ' ' '\n' | grep -qx "$g"; then ok "in group $g"
  elif getent group "$g" >/dev/null; then
    if getent group "$g" | cut -d: -f4 | tr ',' '\n' | grep -qx "$(id -un)"; then
      warn "added to group $g but this login session does not have it yet: log out and back in"
    else fail "not in group $g"; fi
  else warn "group $g does not exist"; fi
done

section "GNS3 software"
if have gns3; then ok "GNS3 GUI: $(gns3 --version 2>/dev/null | tail -1)"; else fail "gns3 (GUI) not found"; fi
if have gns3server; then ok "GNS3 server: $(gns3server --version 2>/dev/null | tail -1)"; else fail "gns3server not found"; fi
gui_v=$(gns3 --version 2>/dev/null | tail -1); srv_v=$(gns3server --version 2>/dev/null | tail -1)
[ -n "$gui_v" ] && [ -n "$srv_v" ] && [ "$gui_v" != "$srv_v" ] && fail "GUI $gui_v and server $srv_v versions differ"
info "Installed from: $(dpkg -S "$(command -v gns3server 2>/dev/null)" 2>/dev/null | cut -d: -f1 || echo 'not a .deb (pip/pipx?)')"

if have ubridge; then
  ok "ubridge: $(ubridge -v 2>/dev/null | head -1)"
  caps=$(getcap "$(command -v ubridge)" 2>/dev/null)
  perms=$(stat -c '%A %U:%G' "$(command -v ubridge)")
  info "ubridge perms: $perms ${caps:-no capabilities}"
  if echo "$caps" | grep -q cap_net_admin || echo "$perms" | grep -q '^...s'; then ok "ubridge can create links (cap_net_admin or setuid)"
  else fail "ubridge has no cap_net_admin/setuid: nodes will fail to connect (sudo dpkg-reconfigure ubridge)"; fi
else fail "ubridge not found (every GNS3 link needs it)"; fi

for b in dynamips vpcs qemu-system-x86_64 qemu-img mcopy docker; do
  if have "$b"; then ok "$b: $($b --version 2>&1 | head -1)"; else
    case $b in
      dynamips) fail "dynamips missing: the built-in Ethernet switch (MGMT-SW) needs it";;
      mcopy) fail "mcopy missing: sudo apt install mtools (needed to load IOSv startup configs)";;
      vpcs) warn "vpcs missing (not used by this lab)";;
      *) fail "$b missing";;
    esac
  fi
done
if have docker; then
  if docker info >/dev/null 2>&1; then ok "Docker daemon reachable without sudo"
  else fail "Docker installed but not usable as $(id -un) (daemon down or not in group docker)"; fi
  for img in gns3/ipterm nginx:alpine; do
    docker image inspect "$img" >/dev/null 2>&1 && ok "docker image $img present" || info "docker image $img not pulled yet (GNS3 pulls it on first start)"
  done
fi
if have aa-status 2>/dev/null; then
  aa-status 2>/dev/null | grep -qi ubridge && warn "AppArmor has a ubridge profile: check 'journalctl -k | grep -i apparmor' if links fail"
fi

section "GNS3 config files"
cfgdir=$(ls -d "$HOME"/.config/GNS3/* 2>/dev/null | sort -V | tail -1)
if [ -z "$cfgdir" ]; then fail "No ~/.config/GNS3/<version> folder: open the GNS3 GUI once"; else
  ok "Config folder: $cfgdir"
  srvconf="$cfgdir/gns3_server.conf"
  if [ -f "$srvconf" ]; then
    info "--- gns3_server.conf (passwords masked) ---"
    grep -vE '^\s*(#|$)' "$srvconf" | mask | sed 's/^/      /'
    grep -qiE '^\s*host\s*=\s*0\.0\.0\.0' "$srvconf" && warn "Server listens on all interfaces (0.0.0.0); 127.0.0.1 is safer"
    proj=$(grep -E '^\s*projects_path' "$srvconf" | cut -d= -f2- | xargs)
    imgs=$(grep -E '^\s*images_path' "$srvconf" | cut -d= -f2- | xargs)
  else warn "No gns3_server.conf"; fi
  ctlconf="$cfgdir/gns3_controller.conf"
  if [ -f "$ctlconf" ]; then
    info "--- GNS3 VM settings (gns3_controller.conf) ---"
    python3 - "$ctlconf" <<'PY'
import json, sys
try:
    c = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"      could not read: {e}"); sys.exit()
vm = c.get("gns3vm", {})
for k in ("enable", "engine", "vmname", "vcpus", "ram", "when_exit", "headless", "port"):
    if k in vm: print(f"      gns3vm.{k} = {vm[k]}")
for comp in c.get("computes", []):
    print(f"      compute: {comp.get('name')} {comp.get('protocol')}://{comp.get('host')}:{comp.get('port')}")
if vm.get("enable"):
    print("WARN  GNS3 VM is ENABLED. On Linux with KVM it is not needed and costs RAM + nested virtualization.")
else:
    print("OK    GNS3 VM is disabled (recommended on Linux: run nodes on the local server)")
PY
  else info "No gns3_controller.conf yet"; fi
  guiconf="$cfgdir/gns3_gui.conf"
  [ -f "$guiconf" ] && info "GUI config: $guiconf"
fi

section "GNS3 VM (if installed)"
found_vm=0
if have VBoxManage; then
  VBoxManage list vms 2>/dev/null | grep -i gns3 && found_vm=1
  info "VirtualBox: $(VBoxManage --version 2>/dev/null)  running: $(VBoxManage list runningvms 2>/dev/null | grep -ci gns3) GNS3 VM(s)"
fi
if have vmrun; then
  vmrun list 2>/dev/null | grep -i gns3 && found_vm=1
  info "VMware: $(vmware --version 2>/dev/null | head -1)"
fi
if have virsh; then
  virsh -c qemu:///system list --all 2>/dev/null | grep -i gns3 && found_vm=1
fi
ls "$HOME"/GNS3/*.ova "$HOME"/Downloads/GNS3*VM* 2>/dev/null | head -3 | sed 's/^/      found file: /'
[ $found_vm -eq 0 ] && info "No GNS3 VM registered in VirtualBox/VMware/libvirt"
if have VBoxManage && lsmod | grep -q '^vboxdrv' && lsmod | grep -qE '^kvm_(intel|amd)'; then
  warn "VirtualBox and KVM modules are both loaded: older VirtualBox cannot run VMs while KVM holds VT-x"
fi

section "Running processes and ports"
pgrep -af gns3server | grep -v pgrep | sed 's/^/      /' || info "gns3server not running (the GUI starts it)"
if have ss; then
  ss -ltnp 2>/dev/null | grep -E ':(3080|3081)\b' | sed 's/^/      /' || info "Nothing listening on 3080/3081"
fi
if have curl; then
  v=$(curl -s -m 3 http://127.0.0.1:3080/v2/version)
  if [ -n "$v" ]; then ok "Local GNS3 API answers: $(echo "$v" | tr -d '\n ' )"
  else info "Local GNS3 API on 127.0.0.1:3080 did not answer (fine if the GUI is closed; 401 means auth on)"; fi
fi

section "Images for the NetWarden lab"
for d in "${imgs:-}" "$HOME/GNS3/images"; do
  [ -n "$d" ] && [ -d "$d" ] && { imgdir="$d"; break; }
done
if [ -n "${imgdir:-}" ]; then
  ok "Images folder: $imgdir"
  ls -la "$imgdir/QEMU" 2>/dev/null | grep -viE 'empty|OVMF|^total' | sed 's/^/      /'
  for f in "$LAB_IOSV" "$LAB_IOSVL2"; do
    if [ -f "$imgdir/QEMU/$f" ]; then ok "image $f present ($(du -h "$imgdir/QEMU/$f" | cut -f1))"
    else fail "image $f missing in $imgdir/QEMU (or set LAB_IOSV_IMAGE / LAB_IOSVL2_IMAGE in .env)"; fi
  done
else fail "No GNS3 images folder found"; fi

section "Management network for the agent (tap-nw)"
if ip link show tap-nw >/dev/null 2>&1; then
  ok "tap-nw exists: $(ip -br addr show tap-nw | xargs)"
  ip -br addr show tap-nw | grep -q '10\.99\.0\.1/24' || fail "tap-nw does not have 10.99.0.1/24"
  [ -c /dev/net/tun ] && ok "/dev/net/tun present"
else
  warn "tap-nw not created yet (see lab/README.md one-time host setup)"
fi
ip route | grep -q '10\.99\.0\.0/24' && ok "Host route to 10.99.0.0/24: $(ip route | grep '10\.99\.0\.0/24' | xargs)"
ip -br addr | grep -E '10\.99\.0\.' | grep -v tap-nw | sed 's/^/WARN  10.99.0.x also on another interface: /'
info "Default route: $(ip route | awk '/^default/ {print $3 " via " $5; exit}')"

section "Summary"
echo "      Review FAIL lines first, then WARN. Paste everything back to Claude."
