/* NetLab: IPv4 router (P4_16, v1model / BMv2)
 *
 * Pipeline:  Parser(ethernet -> ipv4) -> Ingress[ipv4_lpm] -> Egress -> Checksum -> Deparser
 *
 * ipv4_lpm — eng uzun prefiks mosligi (LPM). Har yozuv: keyingi hop MAC + chiqish porti.
 *     table_add ipv4_lpm ipv4_forward 10.0.1.0/24 => 00:00:00:00:01:01 1
 *     table_add ipv4_lpm ipv4_forward 10.0.2.0/24 => 00:00:00:00:02:01 2
 * Eslatma: bu switch ARP'ga javob bermaydi — hostlarda gateway uchun statik ARP
 * kerak (masalan: arp -s 10.0.1.254 00:00:00:00:aa:01) yoki ARP'ni P4'da yozing!
 */
#include <core.p4>
#include <v1model.p4>

const bit<16> TYPE_IPV4 = 0x800;

header ethernet_t {
    bit<48> dstAddr;
    bit<48> srcAddr;
    bit<16> etherType;
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> totalLen;
    bit<16> identification;
    bit<3>  flags;
    bit<13> fragOffset;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> hdrChecksum;
    bit<32> srcAddr;
    bit<32> dstAddr;
}

struct headers_t  { ethernet_t ethernet; ipv4_t ipv4; }
struct metadata_t { }

parser MyParser(packet_in pkt, out headers_t hdr,
                inout metadata_t meta, inout standard_metadata_t std) {
    state start {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            TYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }
    state parse_ipv4 {
        pkt.extract(hdr.ipv4);
        transition accept;
    }
}

control MyVerifyChecksum(inout headers_t hdr, inout metadata_t meta) { apply { } }

control MyIngress(inout headers_t hdr, inout metadata_t meta,
                  inout standard_metadata_t std) {
    register<bit<32>>(512) port_pkts;   // har port uchun paket hisoblagich (register)

    action drop() { mark_to_drop(std); }

    action ipv4_forward(bit<48> dstAddr, bit<9> port) {
        std.egress_spec = port;
        hdr.ethernet.srcAddr = hdr.ethernet.dstAddr;
        hdr.ethernet.dstAddr = dstAddr;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    table ipv4_lpm {
        key = { hdr.ipv4.dstAddr: lpm; }
        actions = { ipv4_forward; drop; NoAction; }
        size = 1024;
        default_action = drop();
    }

    apply {
        bit<32> c;
        port_pkts.read(c, (bit<32>) std.ingress_port);
        port_pkts.write((bit<32>) std.ingress_port, c + 1);
        if (hdr.ipv4.isValid() && hdr.ipv4.ttl > 1) {
            ipv4_lpm.apply();
        } else {
            drop();
        }
    }
}

control MyEgress(inout headers_t hdr, inout metadata_t meta,
                 inout standard_metadata_t std) { apply { } }

control MyComputeChecksum(inout headers_t hdr, inout metadata_t meta) {
    apply {
        update_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv, hdr.ipv4.totalLen,
              hdr.ipv4.identification, hdr.ipv4.flags, hdr.ipv4.fragOffset,
              hdr.ipv4.ttl, hdr.ipv4.protocol, hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16);
    }
}

control MyDeparser(packet_out pkt, in headers_t hdr) {
    apply {
        pkt.emit(hdr.ethernet);
        pkt.emit(hdr.ipv4);
    }
}

V1Switch(MyParser(), MyVerifyChecksum(), MyIngress(), MyEgress(),
         MyComputeChecksum(), MyDeparser()) main;
