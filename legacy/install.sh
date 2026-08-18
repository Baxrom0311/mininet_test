#!/bin/bash
set -euo pipefail

# ============================================================
#  Real Internet Simulation - Full Installation Script
#  Target: Ubuntu 24.04+ on AWS EC2 (minimum t3.large, 8GB RAM)
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    err "Root kerak: sudo bash install.sh"
    exit 1
fi

TOTAL_RAM=$(free -m | awk '/Mem:/{print $2}')
TOTAL_DISK=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
log "System: RAM=${TOTAL_RAM}MB, Free Disk=${TOTAL_DISK}GB"

if [ "$TOTAL_RAM" -lt 4096 ]; then
    warn "Minimum 4GB RAM tavsiya etiladi. Hozir: ${TOTAL_RAM}MB"
    warn "t3.large (8GB) yoki t3.xlarge (16GB) ishlating"
fi

# ────────────────────────────────────────────────
# 1. System update & base packages
# ────────────────────────────────────────────────
log "System yangilanmoqda..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

log "Asosiy paketlar o'rnatilmoqda..."
apt-get install -y -qq \
    git curl wget unzip software-properties-common apt-transport-https \
    python3 python3-pip python3-venv python3-dev \
    build-essential cmake pkg-config \
    net-tools iproute2 iputils-ping dnsutils traceroute \
    iperf3 tcpdump tshark nmap hping3 \
    jq htop tmux tree \
    libpcap-dev libffi-dev

# ────────────────────────────────────────────────
# 2. Docker
# ────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Docker o'rnatilmoqda..."
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
usermod -aG docker ubuntu 2>/dev/null || true

# ────────────────────────────────────────────────
# 3. Open vSwitch
# ────────────────────────────────────────────────
log "Open vSwitch o'rnatilmoqda..."
apt-get install -y -qq openvswitch-switch openvswitch-common
systemctl enable --now openvswitch-switch

# ────────────────────────────────────────────────
# 4. Mininet
# ────────────────────────────────────────────────
if ! command -v mn &>/dev/null; then
    log "Mininet o'rnatilmoqda..."
    if apt-cache show mininet &>/dev/null 2>&1; then
        apt-get install -y -qq mininet
    else
        cd /tmp
        [ -d mininet ] && rm -rf mininet
        git clone --depth 1 https://github.com/mininet/mininet.git
        cd mininet
        PYTHON=python3 util/install.sh -n
        cd /
    fi
fi

# ────────────────────────────────────────────────
# 5. ONOS SDN Controller (Docker)
# ────────────────────────────────────────────────
log "ONOS Docker image yuklanmoqda..."
docker pull onosproject/onos:2.7.0

# ────────────────────────────────────────────────
# 6. sFlow-RT (Docker)
# ────────────────────────────────────────────────
log "sFlow-RT Docker image yuklanmoqda..."
docker pull sflow/sflow-rt

# ────────────────────────────────────────────────
# 7. FRRouting (future BGP/OSPF uchun)
# ────────────────────────────────────────────────
log "FRRouting o'rnatilmoqda..."
CODENAME=$(lsb_release -s -c 2>/dev/null || echo "noble")
# FRR repo might not have the exact codename, try noble as fallback
if ! apt-cache show frr &>/dev/null 2>&1; then
    curl -s https://deb.frrouting.org/frr/keys.gpg | \
        tee /usr/share/keyrings/frrouting.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr ${CODENAME} frr-stable" | \
        tee /etc/apt/sources.list.d/frr.list
    apt-get update -qq 2>/dev/null || true
fi
apt-get install -y -qq frr frr-pythontools 2>/dev/null || \
    warn "FRRouting o'rnatilmadi - keyinroq qo'lda o'rnating"

# ────────────────────────────────────────────────
# 8. Python paketlari
# ────────────────────────────────────────────────
log "Python paketlari o'rnatilmoqda..."
pip3 install --break-system-packages --quiet \
    requests pandas numpy networkx \
    scapy pyarrow pyshark \
    matplotlib seaborn

# nfstream alohida (C dependency bor)
pip3 install --break-system-packages --quiet nfstream 2>/dev/null || \
    warn "nfstream o'rnatilmadi - pcap analysis uchun pyshark ishlatiladi"

# ────────────────────────────────────────────────
# 9. D-ITG Traffic Generator
# ────────────────────────────────────────────────
if ! command -v ITGSend &>/dev/null; then
    log "D-ITG o'rnatilmoqda..."
    cd /tmp
    [ -d D-ITG ] && rm -rf D-ITG
    git clone --depth 1 https://github.com/traffic-team/D-ITG.git 2>/dev/null || \
        git clone --depth 1 https://github.com/atterdag/D-ITG.git 2>/dev/null || true
    if [ -d D-ITG ]; then
        cd D-ITG/src
        make -j$(nproc) 2>/dev/null || true
        cp -f ../bin/ITG* /usr/local/bin/ 2>/dev/null || true
    fi
    cd /
fi

# ────────────────────────────────────────────────
# 10. Ma'lumotlar kataloglari
# ────────────────────────────────────────────────
log "Ma'lumotlar kataloglari yaratilmoqda..."
mkdir -p /data/{pcap,flows,stats,datasets,onos_logs,sflow_data}
chown -R ubuntu:ubuntu /data

# ────────────────────────────────────────────────
# 11. Tizim sozlamalari (katta tarmoq uchun)
# ────────────────────────────────────────────────
log "Kernel parametrlari sozlanmoqda..."
cat > /etc/sysctl.d/99-mininet.conf << 'SYSCTL'
# Mininet + ONOS uchun optimizatsiya
net.ipv4.ip_forward = 1
net.ipv4.conf.all.arp_filter = 0
net.ipv4.conf.all.rp_filter = 0
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216
net.core.netdev_max_backlog = 5000
fs.file-max = 1000000
SYSCTL
sysctl -p /etc/sysctl.d/99-mininet.conf 2>/dev/null || true

# Increase open file limits
cat > /etc/security/limits.d/99-mininet.conf << 'LIMITS'
*    soft    nofile    100000
*    hard    nofile    100000
root soft    nofile    100000
root hard    nofile    100000
LIMITS

# ────────────────────────────────────────────────
# Yakuniy tekshirish
# ────────────────────────────────────────────────
echo ""
echo "============================================"
log "O'rnatish tugadi!"
echo "============================================"
echo ""
echo "Tekshirish:"
echo "──────────────────────────────────────────"
printf "  %-20s" "Docker:";      docker --version 2>/dev/null && echo "" || echo "NOT INSTALLED"
printf "  %-20s" "OVS:";         ovs-vsctl --version 2>/dev/null | head -1 || echo "NOT INSTALLED"
printf "  %-20s" "Mininet:";     mn --version 2>/dev/null || echo "NOT INSTALLED"
printf "  %-20s" "ONOS image:";  docker images onosproject/onos:2.7.0 --format "{{.Repository}}:{{.Tag}} ({{.Size}})" 2>/dev/null || echo "NOT PULLED"
printf "  %-20s" "sFlow-RT:";    docker images sflow/sflow-rt --format "{{.Repository}}:{{.Tag}} ({{.Size}})" 2>/dev/null || echo "NOT PULLED"
printf "  %-20s" "FRR:";         vtysh --version 2>/dev/null | head -1 || echo "NOT INSTALLED"
printf "  %-20s" "iperf3:";      iperf3 --version 2>/dev/null | head -1 || echo "NOT INSTALLED"
printf "  %-20s" "Python3:";     python3 --version 2>/dev/null || echo "NOT INSTALLED"
echo "──────────────────────────────────────────"
echo ""
echo "Keyingi qadam:"
echo "  cd ~/mininet"
echo "  sudo python3 run_simulation.py --help"
echo ""
