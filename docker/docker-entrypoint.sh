#!/bin/bash
set -e

# Start OVS
service openvswitch-switch start 2>/dev/null || true

# Wait for OVS
for i in $(seq 1 10); do
    ovs-vsctl show &>/dev/null && break
    sleep 1
done

# Clean previous Mininet state
mn -c 2>/dev/null || true

# Apply sysctl (requires --privileged)
sysctl -p /etc/sysctl.d/99-mininet.conf 2>/dev/null || true

echo "========================================"
echo " Real Internet Simulation v3"
echo " Args: $@"
echo "========================================"

# Run simulation
exec python3 /app/light_simulation.py "$@"
