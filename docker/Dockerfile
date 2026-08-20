FROM ubuntu:24.04

LABEL maintainer="Bahrom0311"
LABEL description="Real Internet Simulation - Mininet + SDN for ML/AI dataset generation"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

# ─── System packages ───
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    git curl wget net-tools iproute2 iputils-ping \
    iperf3 tcpdump tshark hping3 \
    openvswitch-switch openvswitch-common \
    mininet \
    jq htop \
    && rm -rf /var/lib/apt/lists/*

# ─── Python dependencies ───
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# ─── Data directories ───
RUN mkdir -p /data/{pcap,stats,flows,datasets}

# ─── Kernel params (applied at runtime with --privileged) ───
RUN echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.d/99-mininet.conf \
    && echo "net.ipv4.conf.all.arp_filter = 0" >> /etc/sysctl.d/99-mininet.conf \
    && echo "net.ipv4.conf.all.rp_filter = 0" >> /etc/sysctl.d/99-mininet.conf \
    && echo "net.core.rmem_max = 4194304" >> /etc/sysctl.d/99-mininet.conf \
    && echo "net.core.wmem_max = 4194304" >> /etc/sysctl.d/99-mininet.conf

# ─── Application ───
# Barcha root modullarni ko'chiramiz. Aniq ro'yxat o'rniga *.py — yangi modul
# (masalan netutil.py) qo'shilganda COPY ro'yxatini yangilashni unutish natijasida
# konteyner ishga tushishida ModuleNotFoundError bo'lishining oldini oladi.
# .dockerignore hech qanday .py'ni chiqarib tashlamaydi (faqat datasets/png/csv/
# parquet/jsonl/pcap va h.k.), va build konteksti root'da faqat shu 13 modul bor.
WORKDIR /app
COPY *.py /app/

# ─── Entrypoint ───
COPY docker/docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

VOLUME ["/data"]
EXPOSE 6633 6653

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["--topology", "three_as", "--routing", "spf", "--duration", "180"]
