#!/usr/bin/env bash
# Look for the old project name (NetWarden) on this workstation after the rename to Rootvex.
# Read-only: it changes and deletes nothing. Run from the repo root:
#
#   bash scripts/check_old_name.sh
#
# OK    = clean
# OLD   = still uses the old name and should be renamed or removed (the line says how)
# INFO  = expected or harmless (history notes, old folders you may keep for now)

set -u
ok()   { printf 'OK    %s\n' "$*"; }
old()  { printf 'OLD   %s\n' "$*"; FOUND=$((FOUND + 1)); }
info() { printf 'INFO  %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }
FOUND=0
REPO=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "Run this inside the rootvex repo."; exit 1; }
cd "$REPO"

# Lines that mention NetWarden on purpose (history of the name).
HISTORY='Chosen: \*\*NetWarden|renamed from NetWarden|was called NetWarden|old NetWarden URL|NetWarden → Rootvex|NETWARDEN_\* (renamed )?to `?ROOTVEX_|from `~/netwarden`|Retire `~/netwarden`|\(was `netwarden`\)|\(was `netwarden-lab`\)|\(were `NetWarden|\(were `NW-|netwarden-c7200-test\]|projects: \[netwarden-lab|template_prefix: "NetWarden|pre-rename|from before the rename|Renamed from NetWarden|Only history lines still say|old folders `~/netwarden`|new work went on in `~/netwarden`'

section "Repository files ($REPO)"
hits=$(git grep -n -i -E 'netwarden|\bNW-(MGMT|TS|VTI)\b' -- ':!*.pdf' ':!uv.lock' ':!scripts/check_old_name.sh' 2>/dev/null)
bad=$(printf '%s\n' "$hits" | grep -v -E "$HISTORY" | grep -v '^$')
hist=$(printf '%s\n' "$hits" | grep -E "$HISTORY" | grep -c .)
if [ -n "$bad" ]; then old "old name in tracked files:"; printf '%s\n' "$bad" | cut -c1-160 | sed 's/^/        /'
else ok "no old name in tracked files ($hist history line(s) kept on purpose)"; fi
names=$(git ls-files | grep -i netwarden)
[ -n "$names" ] && old "file names: $names" || ok "no tracked file names with the old name"
if command -v pdftotext >/dev/null; then
  # PDF text wraps mid-line, so compare counts with the Markdown it is built from.
  for pair in Rootvex-Manual:MANUAL Rootvex-Design:DESIGN Rootvex-SetupLog:SETUP_LOG; do
    f="docs/${pair%%:*}.pdf"; md="docs/${pair##*:}.md"
    [ -f "$f" ] || continue
    n=$(pdftotext "$f" - 2>/dev/null | grep -o -i netwarden | wc -l)
    m=$(grep -o -i netwarden "$md" 2>/dev/null | wc -l)
    [ "$n" -gt "$m" ] && old "$f is stale ($n mentions, source has $m): uv run scripts/build_pdfs.py" || ok "$f matches $md"
  done
else info "pdftotext not installed; PDFs not checked (sudo apt install poppler-utils)"; fi
if [ -d .git ] && git ls-files --others --exclude-standard | grep -qi netwarden; then
  old "untracked files with the old name: $(git ls-files --others --exclude-standard | grep -i netwarden | head -5 | xargs)"
fi

section "Git"
git remote -v | awk '{print $2}' | sort -u | while read -r url; do
  case "$url" in *[Nn]et[Ww]arden*) old "remote URL $url  -> git remote set-url origin git@github.com:sabroxyy/Rootvex.git";;
                 *) ok "remote $url";; esac
done
info "current branch: $(git branch --show-current)"

section "Settings and environment"
if [ -f .env ]; then
  n=$(grep -c -E '^\s*NETWARDEN_' .env)
  [ "$n" -gt 0 ] && old ".env has $n NETWARDEN_* line(s): sed -i 's/^NETWARDEN_/ROOTVEX_/' .env" || ok ".env has no NETWARDEN_* variables"
  grep -q -E '^\s*LAB_DEVICE_PASSWORD=.+' .env && ok ".env sets LAB_DEVICE_PASSWORD" || info ".env has no LAB_DEVICE_PASSWORD yet"
else info "no .env yet (cp .env.example .env)"; fi
if [ -f config/inventory.local.yaml ]; then
  grep -q 'username: netwarden' config/inventory.local.yaml \
    && old "config/inventory.local.yaml uses username netwarden: sed -i 's/username: netwarden/username: rootvex/' config/inventory.local.yaml" \
    || ok "config/inventory.local.yaml uses the new login"
else info "no config/inventory.local.yaml yet (cp config/inventory.example.yaml config/inventory.local.yaml)"; fi
envs=$(env | grep -E '^NETWARDEN_' | cut -d= -f1 | xargs)
[ -n "$envs" ] && old "shell variables still set: $envs (unset them / remove from your shell profile)" || ok "no NETWARDEN_* in the current shell"
for rc in ~/.bashrc ~/.profile ~/.bash_profile ~/.zshrc; do
  [ -f "$rc" ] && grep -qi netwarden "$rc" && old "$rc mentions netwarden: $(grep -ni netwarden "$rc" | head -3 | xargs)"
done
command -v netwarden >/dev/null && old "an old 'netwarden' command is installed: $(command -v netwarden)" || ok "no old 'netwarden' command on PATH"
if command -v uv >/dev/null && [ -f pyproject.toml ]; then
  uv run --quiet python -c "import netwarden" 2>/dev/null && old "old 'netwarden' package importable in .venv (rm -rf .venv && uv sync)" || ok ".venv has no old package"
fi
ls logs/netwarden-*.jsonl >/dev/null 2>&1 && info "old audit logs logs/netwarden-*.jsonl (history; delete if you don't need them)"

section "GNS3"
conf=$(ls ~/.config/GNS3/*/gns3_server.conf 2>/dev/null | sort -V | tail -1)
if [ -n "$conf" ] && command -v curl >/dev/null; then
  user=$(awk -F' *= *' '/^user/ {print $2}' "$conf"); pass=$(awk -F' *= *' '/^password/ {print $2}' "$conf")
  api() { curl -s -m 5 -u "$user:$pass" "http://127.0.0.1:3080/v2/$1"; }
  if api version | grep -q version; then
    t=$(api templates | python3 -c 'import sys,json;print("\n".join(t["name"] for t in json.load(sys.stdin) if "netwarden" in t["name"].lower()))')
    p=$(api projects | python3 -c 'import sys,json;print("\n".join(p["name"] for p in json.load(sys.stdin) if "netwarden" in p["name"].lower()))')
    [ -n "$t" ] && old "GNS3 templates: $(echo $t | xargs)  -> uv run python lab/build_gns3_lab.py --remove-old" || ok "no old GNS3 templates"
    [ -n "$p" ] && old "GNS3 projects: $(echo $p | xargs)  -> uv run python lab/build_gns3_lab.py --remove-old" || ok "no old GNS3 projects"
  else info "GNS3 server not running; open the GNS3 GUI and run this again to check templates/projects"; fi
fi
for d in ~/GNS3/projects/*; do
  case "$(basename "$d")" in *[Nn]et[Ww]arden*) old "GNS3 project folder $d (delete it in GNS3, or remove the folder)";; esac
done

section "Folders and Docker"
for d in ~/netwarden ~/projects/netwarden ~/Desktop/NetWarden; do
  [ -e "$d" ] && info "old folder still exists: $d ($(du -sh "$d" 2>/dev/null | cut -f1)) - keep until you retire it"
done
find ~ -maxdepth 3 -iname '*netwarden*' -not -path '*/.cache/*' -not -path "$REPO/*" 2>/dev/null \
  | grep -v -E "^$HOME/(netwarden|projects/netwarden|Desktop/NetWarden)$" | head -10 | sed 's/^/INFO  other path: /'
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  dk=$(docker ps -a --format '{{.Names}} {{.Image}}' | grep -i netwarden)
  [ -n "$dk" ] && old "Docker containers: $dk" || ok "no Docker containers with the old name"
fi

section "Summary"
[ "$FOUND" -eq 0 ] && echo "      No old names left that need action." || echo "      $FOUND item(s) marked OLD need action (see the hints above)."
