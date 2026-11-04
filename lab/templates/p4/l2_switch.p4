/* NetLab: L2 switch (P4_16, v1model / BMv2)
 *
 * Pipeline:  Parser(ethernet) -> Ingress[dmac jadvali] -> Egress[filtr] -> Deparser
 *
 * dmac jadvali — MAC manzil bo'yicha aniq (exact) moslik:
 *     table_add dmac forward 00:00:00:00:01:01 => 1
 * Topilmasa — broadcast(): 1-multicast guruh (NetLab switchning barcha
 * portlarini avtomatik shu guruhga qo'shadi). Egress kirgan portga qaytarmaydi.
 *
 * NetLab MAC'lari deterministik: 00:00:00:<id hi>:<id lo>:<port>
 */
#include <core.p4>
#include <v1model.p4>

header ethernet_t {
    bit<48> dstAddr;
    bit<48> srcAddr;
    bit<16> etherType;
}

struct headers_t  { ethernet_t ethernet; }
struct metadata_t { }

parser MyParser(packet_in pkt, out headers_t hdr,
                inout metadata_t meta, inout standard_metadata_t std) {
    state start {
        pkt.extract(hdr.ethernet);
        transition accept;
    }
}

control MyVerifyChecksum(inout headers_t hdr, inout metadata_t meta) { apply { } }

control MyIngress(inout headers_t hdr, inout metadata_t meta,
                  inout standard_metadata_t std) {
    counter(512, CounterType.packets_and_bytes) port_rx;

    action drop() { mark_to_drop(std); }
    action forward(bit<9> port) { std.egress_spec = port; }
    action broadcast() { std.mcast_grp = 1; }

    table dmac {
        key = { hdr.ethernet.dstAddr: exact; }
        actions = { forward; broadcast; drop; }
        size = 1024;
        default_action = broadcast();
    }

    apply {
        port_rx.count((bit<32>) std.ingress_port);
        dmac.apply();
    }
}

control MyEgress(inout headers_t hdr, inout metadata_t meta,
                 inout standard_metadata_t std) {
    apply {
        // Multicast nusxasini kelgan portga qaytarmaslik (L2 halqa oldini olish)
        if (std.egress_port == std.ingress_port) {
            mark_to_drop(std);
        }
    }
}

control MyComputeChecksum(inout headers_t hdr, inout metadata_t meta) { apply { } }

control MyDeparser(packet_out pkt, in headers_t hdr) {
    apply { pkt.emit(hdr.ethernet); }
}

V1Switch(MyParser(), MyVerifyChecksum(), MyIngress(), MyEgress(),
         MyComputeChecksum(), MyDeparser()) main;
