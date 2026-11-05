"""Small ns-3 adapter for IPv4/IPv6; packet/queue simulation stays shared."""

import ipaddress

from lab.engines.nodestats import route_key


class AddressFamily:
    def __init__(self, ns, version):
        self.ns, self.version = ns, version
        self.ip_type = ns.Ipv6 if version == 6 else ns.Ipv4
        self.address = ns.Ipv6Address if version == 6 else ns.Ipv4Address
        self.socket = ns.Inet6SocketAddress if version == 6 else ns.InetSocketAddress
        self.helper = ns.Ipv6StaticRoutingHelper() if version == 6 else ns.Ipv4StaticRoutingHelper()

    def configure_stack(self, stack):
        if self.version == 6:
            stack.SetIpv4StackInstall(False)
            stack.SetRoutingHelper(self.helper)
        else:
            stack.SetIpv6StackInstall(False)

    def protocol(self, node):
        return node.GetObject[self.ip_type]()

    def bind(self, ip, device, address, forwarding):
        idx = ip.GetInterfaceForDevice(device)
        if idx < 0:
            idx = ip.AddInterface(device)
        ii = ipaddress.ip_interface(address) if address else None
        if ii:
            if self.version == 6:
                if ii.ip in ipaddress.ip_network("2001:db8::/32"):
                    raise ValueError("ns-3 drops forwarded 2001:db8::/32; use ULA prefixes (fd00::/8)")
                ia = self.ns.Ipv6InterfaceAddress(self.address(str(ii.ip)), self.ns.Ipv6Prefix(ii.network.prefixlen))
            else:
                ia = self.ns.Ipv4InterfaceAddress(self.address(str(ii.ip)), self.ns.Ipv4Mask(str(ii.netmask)))
            ip.AddAddress(idx, ia)
        if self.version == 6:
            ip.SetAttribute("IpForward", self.ns.BooleanValue(forwarding))
            ip.SetForwarding(idx, forwarding)
        ip.SetUp(idx)
        return idx, ii

    def set_link_state(self, ip, index, up, address):
        if not up:
            ip.SetDown(index)
            return
        ip.SetUp(index)
        # ns-3 Ipv6Interface::SetDown clears all addresses (IPv4 retains them).
        # Restore the configured global/ULA address before sockets run again.
        if self.version == 6 and address and not any(
                str(ip.GetAddress(index, i).GetAddress()) == str(address.ip)
                for i in range(ip.GetNAddresses(index))):
            ip.AddAddress(index, self.ns.Ipv6InterfaceAddress(
                self.address(str(address.ip)), self.ns.Ipv6Prefix(address.network.prefixlen)))

    def routes(self, node):
        return self.helper.GetStaticRouting(self.protocol(node))

    def clear_gateway_routes(self, routing):
        for i in range(routing.GetNRoutes() - 1, -1, -1):
            if not routing.GetRoute(i).GetGateway().IsAny():
                routing.RemoveRoute(i)

    def add_route(self, routing, network, via, index):
        prefix = (self.ns.Ipv6Prefix(network.prefixlen) if self.version == 6
                  else self.ns.Ipv4Mask(str(network.netmask)))
        routing.AddNetworkRouteTo(self.address(str(network.network_address)), prefix,
                                  self.address(str(via)), index)

    def endpoint(self, address, port):
        return self.socket(self.address(address), port).ConvertTo()

    def any_endpoint(self, port):
        return self.socket(self.address.GetAny(), port).ConvertTo()

    def classifier(self, monitor_helper):
        if self.version == 6:
            return self.ns.DynamicCast[self.ns.Ipv6FlowClassifier](monitor_helper.GetClassifier6())
        return self.ns.DynamicCast[self.ns.Ipv4FlowClassifier](monitor_helper.GetClassifier())

    def route_keys(self, node, index_names):
        """Read actual IPv6 static routes without parsing PrintRoutingTable text."""
        routing = self.routes(node)
        out = set()
        for i in range(routing.GetNRoutes()):
            entry = routing.GetRoute(i)
            prefix = entry.GetDestNetworkPrefix().GetPrefixLength()
            network = ipaddress.ip_network(f"{entry.GetDestNetwork()}/{prefix}", strict=False)
            if network.is_link_local or network.is_multicast or network.is_loopback:
                continue
            gateway = entry.GetGateway()
            out.add(route_key(network, None if gateway.IsAny() else str(gateway),
                              index_names.get(entry.GetInterface(), str(entry.GetInterface()))))
        return out
