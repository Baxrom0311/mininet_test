"""Check known JSON containers before protocol validators traverse a project.

Unknown metadata is deliberately retained. Optional null containers use the
same absent-value defaults as the adapters; entries inside them must be objects.
"""

import copy


class _Shape:
    def __init__(self):
        self.errors = []

    def mapping(self, value, path, optional=False):
        if value is None and optional:
            return {}
        if not isinstance(value, dict):
            self.errors.append(f"{path}: object required")
            return {}
        if any(not isinstance(key, str) for key in value):
            self.errors.append(f"{path}: object keys must be strings")
            return {}
        return value

    def array(self, value, path):
        if value is None:
            return []
        if not isinstance(value, list):
            self.errors.append(f"{path}: array required")
            return []
        return value

    def objects(self, parent, key, path):
        return [(name, self.mapping(item, f"{path}.{key}.{name}"))
                for name, item in self.mapping(parent.get(key), f"{path}.{key}", optional=True).items()]

    def records(self, parent, key, path):
        return [(i, self.mapping(item, f"{path}.{key}[{i}]"))
                for i, item in enumerate(self.array(parent.get(key), f"{path}.{key}"))]

    def strings(self, obj, fields, path):
        for key in fields:
            if obj.get(key) is not None and not isinstance(obj[key], str):
                self.errors.append(f"{path}.{key}: string required")

    def names(self, obj, key, path):
        values = self.array(obj.get(key), f"{path}.{key}")
        for i, value in enumerate(values):
            if not isinstance(value, str):
                self.errors.append(f"{path}.{key}[{i}]: string required")

    def booleans(self, obj, fields, path):
        for key in fields:
            if key in obj and type(obj[key]) is not bool:
                self.errors.append(f"{path}.{key}: boolean required")


def validate_shape(project):
    """Return shape errors without coercing, removing or changing user fields."""
    s = _Shape()
    p = s.mapping(project, "project")
    if not isinstance(project, dict):
        return s.errors
    if not isinstance(p.get("name"), str):
        s.errors.append("project.name: string required")
    devs = s.mapping(p.get("devices"), "devices")
    links = s.mapping(p.get("links"), "links")
    s.mapping(p.get("scenarios"), "scenarios", optional=True)
    for name, raw in devs.items():
        path = f"devices.{name}"
        d = s.mapping(raw, path)
        s.strings(d, ("type",), path)
        cfg = s.mapping(d.get("config"), f"{path}.config", optional=True)
        cp = f"{path}.config"
        s.strings(cfg, ("gateway", "gateway6", "mode", "controller", "program", "runtime",
                        "stp_mode", "standard", "ssid"), cp)
        if cfg.get("startup") is not None and not isinstance(cfg["startup"], str):
            # Preserve the established OSPF error used by callers and tests.
            s.errors.append(f"{name}: OSPF startup must be text")
        s.booleans(cfg, ("stp", "igmp_snooping", "ip_forward"), cp)
        s.names(cfg, "daemons", cp)
        for ifn, ic in s.objects(cfg, "interfaces", cp):
            s.strings(ic, ("ip", "ip6", "mac"), f"{cp}.interfaces.{ifn}")
        for i, route in s.records(cfg, "routes", cp):
            s.strings(route, ("prefix", "via"), f"{cp}.routes[{i}]")
        for port, pc in s.objects(cfg, "ports", cp):
            s.strings(pc, ("mode",), f"{cp}.ports.{port}")
            s.array(pc.get("allowed"), f"{cp}.ports.{port}.allowed")
        for bond, opts in s.objects(cfg, "bonds", cp):
            s.strings(opts, ("mode", "lacp"), f"{cp}.bonds.{bond}")
            s.array(opts.get("ports"), f"{cp}.bonds.{bond}.ports")
        s.mapping(cfg.get("rip_timers"), f"{cp}.rip_timers", optional=True)
        bgp = s.mapping(cfg.get("bgp"), f"{cp}.bgp", optional=True)
        s.strings(bgp, ("router_id",), f"{cp}.bgp")
        for i, nb in s.records(bgp, "neighbors", f"{cp}.bgp"):
            s.strings(nb, ("ip",), f"{cp}.bgp.neighbors[{i}]")
            s.booleans(nb, ("next_hop_self",), f"{cp}.bgp.neighbors[{i}]")
        for key in ("networks", "redistribute"):
            s.names(bgp, key, f"{cp}.bgp")
        isis = s.mapping(cfg.get("isis"), f"{cp}.isis", optional=True)
        s.strings(isis, ("net", "tag", "level"), f"{cp}.isis")
        for ifn, opts in s.objects(isis, "interfaces", f"{cp}.isis"):
            s.booleans(opts, ("passive",), f"{cp}.isis.interfaces.{ifn}")
        pim = s.mapping(cfg.get("pim"), f"{cp}.pim", optional=True)
        s.strings(pim, ("rp",), f"{cp}.pim")
        s.names(pim, "interfaces", f"{cp}.pim")
        nat = s.mapping(cfg.get("nat"), f"{cp}.nat", optional=True)
        s.strings(nat, ("outside",), f"{cp}.nat")
        dhcp = s.mapping(cfg.get("dhcp_server"), f"{cp}.dhcp_server", optional=True)
        for i, pool in s.records(dhcp, "pools", f"{cp}.dhcp_server"):
            s.strings(pool, ("iface", "start", "end", "gateway"), f"{cp}.dhcp_server.pools[{i}]")
        fw = s.mapping(cfg.get("firewall"), f"{cp}.firewall", optional=True)
        s.strings(fw, ("default",), f"{cp}.firewall")
        policy = s.mapping(fw.get("policy"), f"{cp}.firewall.policy", optional=True)
        s.strings(policy, tuple(policy), f"{cp}.firewall.policy")
        for i, rule in s.records(fw, "rules", f"{cp}.firewall"):
            s.strings(rule, ("chain", "action", "proto", "state", "src", "dst", "in", "out"),
                      f"{cp}.firewall.rules[{i}]")
        for i, entry in s.records(cfg, "vrrp", cp):
            s.strings(entry, ("iface", "vip"), f"{cp}.vrrp[{i}]")
            s.booleans(entry, ("preempt",), f"{cp}.vrrp[{i}]")
    for lid, raw in links.items():
        path = f"links.{lid}"
        link = s.mapping(raw, path)
        for side in ("a", "b"):
            end = s.mapping(link.get(side), f"{path}.{side}")
            s.strings(end, ("dev",), f"{path}.{side}")
        s.mapping(link.get("params"), f"{path}.params", optional=True)
        s.booleans(link, ("up",), path)
    for name, controller in s.objects(p, "controllers", "project"):
        s.strings(controller, ("code",), f"controllers.{name}")
    programs = s.mapping(p.get("p4_programs"), "p4_programs", optional=True)
    for name, source in programs.items():
        if not isinstance(source, str):
            s.errors.append(f"p4_programs.{name}: source string required")
    groups = s.mapping(p.get("srlg"), "srlg", optional=True)
    for name in groups:
        s.names(groups, name, "srlg")
    return s.errors


def _omit_null(obj, fields):
    for field in fields:
        if obj.get(field) is None:
            obj.pop(field, None)


def project_for_execution(project):
    """Copy a validated project, applying only known absent-value conventions.

    Several adapters use get(key, default), others use get(key) or default. Make
    explicit optional nulls behave alike. Requested drafts/opaque metadata stay
    unchanged, and healthy documents acquire no additional link defaults.
    """
    p = copy.deepcopy(project)
    # Runtime control state is created by engines, never trusted from a draft.
    for field in ("_stp_hold", "_lag_hold", "_vrrp_master"):
        p.pop(field, None)
    _omit_null(p, ("controllers", "p4_programs", "scenarios", "srlg", "meters_per_px"))
    for d in p["devices"].values():
        _omit_null(d, ("x", "y"))
        if d.get("config") is None:
            d["config"] = {}
        cfg = d["config"]
        _omit_null(cfg, ("interfaces", "ports", "bonds", "rip_timers", "bgp", "dhcp_server", "nat",
                         "firewall", "pim", "isis", "daemons", "routes", "vrrp", "startup", "gateway",
                         "gateway6", "mode", "controller", "program", "runtime", "stp_mode",
                         "standard", "ssid", "band", "width", "tx_power", "channel", "stp_priority"))
        for ic in cfg.get("interfaces", {}).values():
            _omit_null(ic, ("ip", "ip6", "mac"))
        for pc in cfg.get("ports", {}).values():
            _omit_null(pc, ("mode", "allowed", "native", "stp_cost"))
        for bond in cfg.get("bonds", {}).values():
            _omit_null(bond, ("ports", "mode", "lacp"))
        if "bgp" in cfg:
            _omit_null(cfg["bgp"], ("neighbors", "networks", "redistribute", "router_id"))
        if "isis" in cfg:
            _omit_null(cfg["isis"], ("interfaces",))
        if "dhcp_server" in cfg:
            _omit_null(cfg["dhcp_server"], ("pools",))
        if "firewall" in cfg:
            _omit_null(cfg["firewall"], ("rules", "policy"))
        if "pim" in cfg:
            _omit_null(cfg["pim"], ("interfaces", "rp"))
    for link in p["links"].values():
        if link.get("params") is None:
            link.pop("params", None)
        else:
            params = link["params"]
            for key in ("bw", "delay", "jitter", "loss", "queue", "medium", "qdisc"):
                if params.get(key) in (None, ""):
                    params.pop(key, None)
    for controller in p.get("controllers", {}).values():
        _omit_null(controller, ("code",))
    return p
