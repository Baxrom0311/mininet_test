#!/bin/bash
set -euo pipefail

# ============================================================
#  Light Install - 1GB RAM VM uchun optimallashtirilgan
#  Ryu/os-ken + Mininet + traffic tools
# ============================================================

if [ "$EUID" -ne 0 ]; then
    echo "Root kerak: sudo bash install_light.sh"
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "[1/6] System update..."
apt-get update -qq
apt-get install -y -qq \
    git curl wget python3 python3-pip python3-venv python3-dev \
    build-essential net-tools iproute2 iputils-ping \
    iperf3 tcpdump tshark hping3 \
    jq htop tmux

echo "[2/6] Open vSwitch..."
apt-get install -y -qq openvswitch-switch openvswitch-common
systemctl enable --now openvswitch-switch

echo "[3/6] Mininet..."
apt-get install -y -qq mininet 2>/dev/null || {
    cd /tmp
    [ -d mininet ] && rm -rf mininet
    git clone --depth 1 https://github.com/mininet/mininet.git
    cd mininet
    PYTHON=python3 util/install.sh -n
    cd /
}

echo "[4/6] Python paketlari..."
pip3 install --break-system-packages --quiet \
    eventlet msgpack ovs ryu 2>/dev/null || true
pip3 install --break-system-packages --quiet \
    os-ken 2>/dev/null || true
pip3 install --break-system-packages --quiet \
    requests pandas numpy networkx scapy pyarrow

echo "[5/6] Kataloglar..."
mkdir -p /data/{pcap,stats,flows,datasets}
chown -R ubuntu:ubuntu /data

echo "[6/6] Kernel tuning..."
cat > /etc/sysctl.d/99-mininet.conf << 'EOF'
net.ipv4.ip_forward = 1
net.ipv4.conf.all.arp_filter = 0
net.ipv4.conf.all.rp_filter = 0
net.core.rmem_max = 4194304
net.core.wmem_max = 4194304
fs.file-max = 100000
EOF
sysctl -p /etc/sysctl.d/99-mininet.conf 2>/dev/null || true

echo ""
echo "============================================"
echo " O'rnatish tugadi!"
echo "============================================"
printf "  %-15s" "OVS:";     ovs-vsctl --version 2>/dev/null | head -1 || echo "NONE"
printf "  %-15s" "Mininet:"; mn --version 2>/dev/null || echo "NONE"
printf "  %-15s" "Python:";  python3 --version 2>/dev/null || echo "NONE"
printf "  %-15s" "iperf3:";  iperf3 --version 2>/dev/null | head -1 || echo "NONE"

# Controller tekshirish
if python3 -c "import ryu" 2>/dev/null; then
    echo "  Controller:     ryu"
elif python3 -c "import os_ken" 2>/dev/null; then
    echo "  Controller:     os-ken"
else
    echo "  Controller:     NOT FOUND"
fi
echo "============================================"
