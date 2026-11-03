#!/usr/bin/env bash
# NetLab o'rnatish (Ubuntu 22.04 / 24.04, shu jumladan WSL2).
#
#   sudo bash lab/install_lab.sh            # asos: Mininet + OVS + tcpdump + backend
#   sudo bash lab/install_lab.sh --all      # + FRR + os-ken (SDN) + BMv2/p4c (P4)
#   sudo bash lab/install_lab.sh --frr --sdn --p4   # tanlab
#
# Backend paketlari lab/.venv ga o'rnatiladi (--system-site-packages — apt'dagi
# mininet/os_ken modullari ham ko'rinadi). Ishga tushirish:
#   sudo lab/.venv/bin/python -m lab.netlab --driver real
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "sudo bilan ishga tushiring"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
WANT_FRR=0; WANT_SDN=0; WANT_P4=0
for a in "$@"; do
  case "$a" in
    --frr) WANT_FRR=1 ;; --sdn) WANT_SDN=1 ;; --p4) WANT_P4=1 ;;
    --all) WANT_FRR=1; WANT_SDN=1; WANT_P4=1 ;;
    *) echo "noma'lum parametr: $a"; exit 1 ;;
  esac
done

. /etc/os-release
echo "== NetLab o'rnatish: $PRETTY_NAME =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y mininet openvswitch-switch tcpdump iperf3 iproute2 net-tools \
  traceroute iputils-ping python3-venv python3-pip curl gnupg
service openvswitch-switch start 2>/dev/null || /usr/share/openvswitch/scripts/ovs-ctl start || true

if [ "$WANT_FRR" -eq 1 ]; then
  echo "== FRRouting =="
  apt-get install -y frr frr-pythontools
  # Tizimdagi FRR xizmati kerak emas — NetLab har router uchun o'z daemonlarini
  # namespace ichida (-N <nom>) ishga tushiradi.
  systemctl disable --now frr 2>/dev/null || service frr stop 2>/dev/null || true
fi

if [ "$WANT_SDN" -eq 1 ]; then
  echo "== os-ken (SDN kontroller) =="
  apt-get install -y python3-os-ken || {
    echo "python3-os-ken topilmadi — pip orqali os-ken o'rnatiladi (venv ichida)"; PIP_EXTRA="os-ken"; }
fi

if [ "$WANT_P4" -eq 1 ]; then
  echo "== P4: BMv2 + p4c (p4lang OBS repozitoriyasi) =="
  REL="xUbuntu_${VERSION_ID}"
  echo "deb http://download.opensuse.org/repositories/home:/p4lang/${REL}/ /" > /etc/apt/sources.list.d/home-p4lang.list
  curl -fsSL "https://download.opensuse.org/repositories/home:p4lang/${REL}/Release.key" | gpg --dearmor > /etc/apt/trusted.gpg.d/home-p4lang.gpg
  apt-get update -y
  apt-get install -y p4lang-p4c p4lang-bmv2 || echo "OGOHLANTIRISH: p4lang paketlari o'rnatilmadi (${REL} uchun mavjud emasmi?)"
fi

echo "== Python backend (lab/.venv) =="
python3 -m venv --system-site-packages "$HERE/.venv"
"$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt" ${PIP_EXTRA:-}

echo
echo "Tayyor. Ishga tushirish:"
echo "  cd $REPO && sudo lab/.venv/bin/python -m lab.netlab --driver real"
echo "So'ng brauzerda: http://127.0.0.1:8080/  (WSL'da ham Windows brauzeridan shu manzil)"
