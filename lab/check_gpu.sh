#!/usr/bin/env bash
# GPU health check for NetWarden (GTX 1060 6 GB, NVIDIA driver 580). No sudo needed.
#
#   bash lab/check_gpu.sh | tee gpu-check.txt          # quick, read-only checks
#   bash lab/check_gpu.sh --load | tee gpu-check.txt   # + real model run on the GPU via Ollama
#
# --load pulls granite4:3b (~2 GB) if missing, runs it for ~1 minute while sampling
# temperature, power, clocks and memory, and checks the model sits 100% on the GPU.
# Each line starts with OK, WARN or FAIL. Paste the output back to Claude.

set -u
ok()   { printf 'OK    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; }
info() { printf '      %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
LOAD=0; [ "${1:-}" = "--load" ] && LOAD=1
MODEL="${NETWARDEN_GPU_TEST_MODEL:-granite4:3b}"
q() { nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs; }

section "PCI device"
if have lspci; then
  gpu=$(lspci -nn | grep -iE 'vga|3d controller' | grep -i nvidia)
  if [ -n "$gpu" ]; then ok "NVIDIA card on the PCI bus: $gpu"
  else fail "No NVIDIA card on the PCI bus (seated? power cables? BIOS primary display?)"; fi
  lspci -nn | grep -iE 'vga|3d controller' | grep -vi nvidia | sed 's/^/      other display adapter: /'
  slot=$(echo "$gpu" | awk '{print $1}')
  if [ -n "$slot" ]; then
    drv=$(lspci -k -s "$slot" 2>/dev/null | awk -F': ' '/Kernel driver in use/ {print $2}')
    [ "$drv" = "nvidia" ] && ok "Kernel driver in use: nvidia" || fail "Kernel driver in use: ${drv:-none} (expected nvidia)"
  fi
else warn "lspci not found (sudo apt install pciutils)"; fi

section "Driver"
if grep -q '^nouveau ' /proc/modules 2>/dev/null; then fail "nouveau (open-source driver) is loaded: it blocks the NVIDIA driver"
else ok "nouveau not loaded"; fi
if grep -q '^nvidia ' /proc/modules 2>/dev/null; then ok "nvidia kernel module loaded: $(cat /proc/driver/nvidia/version 2>/dev/null | head -1 | sed 's/  */ /g')"
else fail "nvidia kernel module not loaded"; fi
for m in nvidia_uvm nvidia_modeset nvidia_drm; do
  grep -q "^$m " /proc/modules && ok "$m loaded" || warn "$m not loaded (nvidia_uvm is needed for CUDA; it loads on first use)"
done
if have mokutil; then
  sb=$(mokutil --sb-state 2>/dev/null)
  info "Secure Boot: ${sb:-unknown}"
  echo "$sb" | grep -qi enabled && warn "Secure Boot is on: the NVIDIA module must be signed (MOK) or it won't load after updates"
fi
info "Installed packages: $(dpkg -l 2>/dev/null | awk '/^ii/ && /nvidia-(driver|utils|dkms|kernel-common)-[0-9]+/ {print $2"="$3}' | xargs)"
pkgs=$(dpkg -l 2>/dev/null | awk '/^ii/ && /nvidia-driver-[0-9]+/ {print $2}' | sort -u | wc -l)
[ "$pkgs" -gt 1 ] && warn "More than one nvidia-driver package installed"
info "Note: GTX 10-series (Pascal) is supported up to the 580 driver branch; do not upgrade to a newer branch."

section "nvidia-smi"
if ! have nvidia-smi; then fail "nvidia-smi not found"; else
  if nvidia-smi >/dev/null 2>&1; then ok "nvidia-smi talks to the driver"
  else fail "nvidia-smi fails: $(nvidia-smi 2>&1 | head -2 | xargs)"; fi
  info "GPU: $(q name) | VBIOS $(q vbios_version) | driver $(q driver_version) | CUDA $(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: [0-9.]+' | cut -d' ' -f3)"
  info "Memory: $(q memory.used) / $(q memory.total) MiB used"
  info "Temp $(q temperature.gpu) C | fan $(q fan.speed) % | power $(q power.draw) / $(q power.limit) W | P-state $(q pstate)"
  info "Clocks now: core $(q clocks.gr) MHz, mem $(q clocks.mem) MHz (max core $(q clocks.max.gr), mem $(q clocks.max.mem))"
  t=$(q temperature.gpu); [ -n "$t" ] && [ "$t" -ge 70 ] 2>/dev/null && warn "GPU is ${t} C while idle: check fan and airflow"
  pg=$(q pcie.link.gen.current); pgm=$(q pcie.link.gen.max); pw=$(q pcie.link.width.current); pwm=$(q pcie.link.width.max)
  info "PCIe now: gen $pg x$pw (max gen $pgm x$pwm). Gen drops at idle are normal; width below max is not."
  [ -n "$pw" ] && [ -n "$pwm" ] && [ "$pw" -lt "$pwm" ] 2>/dev/null && warn "PCIe width x$pw < x$pwm: card may be in a smaller slot or not fully seated"
  thr=$(nvidia-smi -q -d PERFORMANCE 2>/dev/null | grep -E 'Thermal Slowdown|HW Slowdown|Power Brake' | grep -i active)
  [ -n "$thr" ] && warn "Throttling active now: $(echo "$thr" | xargs)" || ok "No hardware/thermal throttling active"
  ecc=$(nvidia-smi -q 2>/dev/null | grep -iE 'Retired Pages|Remapped' | head -2 | xargs)
  [ -n "$ecc" ] && info "Memory health: $ecc"
  info "Processes on the GPU:"
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | sed 's/^/        /'
  nvidia-smi 2>/dev/null | grep -E 'Xorg|gnome-shell' | awk '{print "        display: " $0}' | sed 's/  */ /g'
fi

section "Driver errors in the kernel log (Xid = GPU faults)"
if journalctl -k -b --no-pager -q 2>/dev/null | head -1 >/dev/null && journalctl -k -b --no-pager -q 2>/dev/null | grep -q .; then
  x=$(journalctl -k -b --no-pager -q 2>/dev/null | grep -iE 'NVRM|Xid|nvidia.*(error|fail)' | tail -8)
  if echo "$x" | grep -q 'Xid'; then fail "Xid errors this boot:"; echo "$x" | sed 's/^/        /'
  elif [ -n "$x" ]; then info "NVIDIA kernel messages this boot:"; echo "$x" | sed 's/^/        /'
  else ok "No NVIDIA errors in this boot's kernel log"; fi
else
  info "Kernel log not readable as $(id -un) (add yourself to group adm, or run: sudo journalctl -k -b | grep -i xid)"
fi

section "Ollama"
if ! have ollama; then fail "ollama not installed (curl -fsSL https://ollama.com/install.sh | sh)"; else
  ok "ollama $(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  if systemctl is-active --quiet ollama 2>/dev/null; then ok "ollama service running"
  elif pgrep -x ollama >/dev/null; then ok "ollama running (not as a systemd service)"
  else warn "ollama is not running (sudo systemctl enable --now ollama)"; fi
  if curl -s -m 3 http://127.0.0.1:11434/api/version >/dev/null; then
    ok "Ollama API answers on 127.0.0.1:11434"
    info "Models pulled: $(ollama list 2>/dev/null | awk 'NR>1 {print $1}' | xargs)"
    gpu_log=$(journalctl -u ollama -b --no-pager -q 2>/dev/null | grep -iE 'inference compute|library=cuda|no compatible GPUs|compute capability|cuda.*(fail|error)' | tail -4)
    if echo "$gpu_log" | grep -qiE 'library=cuda|inference compute.*cuda'; then ok "Ollama found the GPU:"; echo "$gpu_log" | sed 's/^/        /'
    elif echo "$gpu_log" | grep -qi 'no compatible'; then fail "Ollama found no compatible GPU:"; echo "$gpu_log" | sed 's/^/        /'
    elif [ -n "$gpu_log" ]; then info "Ollama GPU log:"; echo "$gpu_log" | sed 's/^/        /'
    else info "Ollama log not readable or no GPU line yet (the --load test checks it directly)"; fi
  else warn "Ollama API not answering on 127.0.0.1:11434"; fi
fi

if [ $LOAD -eq 1 ]; then
  section "Load test: $MODEL on the GPU"
  if ! have ollama || ! curl -s -m 3 http://127.0.0.1:11434/api/version >/dev/null; then
    fail "Skipped: Ollama is not running"
  else
    if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
      info "Pulling $MODEL (one time, ~2 GB)..."
      ollama pull "$MODEL" >/dev/null 2>&1 && ok "pulled $MODEL" || fail "could not pull $MODEL"
    fi
    samples=$(mktemp)
    nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.gr,memory.used,utilization.gpu \
      --format=csv,noheader,nounits -l 1 > "$samples" 2>/dev/null &
    smi_pid=$!
    prompt="Explain step by step how to troubleshoot an OSPF neighbor stuck in EXSTART between two Cisco routers. Be detailed."
    resp=$(curl -s -m 300 http://127.0.0.1:11434/api/generate \
      -d "{\"model\":\"$MODEL\",\"prompt\":\"$prompt\",\"stream\":false,\"keep_alive\":\"2m\",\"options\":{\"num_predict\":600}}")
    placement=$(ollama ps 2>/dev/null | awk 'NR==2 {for(i=1;i<=NF;i++) if ($i ~ /GPU|CPU/) {print $(i-1), $i; exit}}')
    sleep 1; kill $smi_pid 2>/dev/null; wait $smi_pid 2>/dev/null
    python3 - "$resp" "$samples" "$placement" <<'PY'
import json, sys
resp, samples, placement = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    r = json.loads(resp)
except Exception:
    print(f"FAIL  No valid answer from Ollama: {resp[:200]}"); sys.exit()
if "error" in r:
    print(f"FAIL  Ollama error: {r['error']}"); sys.exit()
ev, evd = r.get("eval_count", 0), r.get("eval_duration", 1) / 1e9
pc, pcd = r.get("prompt_eval_count", 0), r.get("prompt_eval_duration", 1) / 1e9
tps = ev / evd if evd else 0
print(f"OK    Generated {ev} tokens at {tps:.1f} tokens/s (prompt {pc} tokens at {pc/pcd if pcd else 0:.0f} tok/s)")
print(f"      Load time {r.get('load_duration',0)/1e9:.1f} s, total {r.get('total_duration',0)/1e9:.1f} s")
if placement:
    if "100% GPU" in placement: print(f"OK    Model placement: {placement}")
    elif "GPU" in placement: print(f"WARN  Model partly on CPU: {placement} (model too big for 6 GB VRAM?)")
    else: print(f"FAIL  Model running on CPU only: {placement}")
if tps < 15: print("WARN  Under 15 tokens/s for a 3B model: GPU may not be used, or it is throttling")
rows = []
for line in open(samples):
    try: rows.append([float(x) for x in line.split(",")])
    except ValueError: pass
if rows:
    t = max(x[0] for x in rows); p = max(x[1] for x in rows); c = max(x[2] for x in rows)
    m = max(x[3] for x in rows); u = max(x[4] for x in rows)
    print(f"      Under load (max of {len(rows)} samples): {t:.0f} C, {p:.0f} W, core {c:.0f} MHz, "
          f"VRAM {m:.0f} MiB, utilization {u:.0f} %")
    print(("OK    " if t < 83 else "WARN  ") + f"Peak temperature {t:.0f} C (GTX 1060 slows down around 83 C)")
    print(("OK    " if u >= 50 else "WARN  ") + f"Peak GPU utilization {u:.0f} %")
PY
    rm -f "$samples"
    info "Model stays loaded 2 minutes, then Ollama frees the VRAM."
  fi
else
  info "Run with --load to test a real model on the GPU."
fi

section "Summary"
echo "      Review FAIL lines first, then WARN. Paste everything back to Claude."
