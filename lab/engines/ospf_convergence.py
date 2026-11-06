"""Optional OSPF/OSPFv3 route-install delay shared by analytic and ns-3.

This is a forwarding-table approximation, not an OSPF protocol implementation.
During a hold, keep the last installed OSPF routes and use the current physical
topology. Connected, static and other protocol routes remain current. A second
topology change restarts the hold without installing an intermediate SPF result.
"""

from lab.drivers.demo_driver import Net
from lab.model import ospf_convergence_delay


def _is_ospf(route):
    return route[3].split(" ", 1)[0] in ("OSPF", "OSPFv3")


class _HeldNet(Net):
    def __init__(self, current, saved):
        super().__init__(current.p)
        self.current, self.saved = current, saved

    def routes(self, dev):
        live = self.current.routes(dev)
        if dev not in self.saved:
            return live
        live = [r for r in live if not _is_ospf(r)]
        known = {r[0] for r in live}
        return live + [r for r in self.saved[dev] if r[0] not in known]


class OspfConvergence:
    def __init__(self, sc):
        self.delay_s = ospf_convergence_delay(sc)
        self.deadline = None
        self.saved = {}

    def before_change(self, project, t):
        """Call before an effective link up/down, never for an unchanged link."""
        if not self.delay_s:
            return
        if self.deadline is None or self.due(t):
            n = Net(project)
            daemon = "ospf6d" if n.ip_version == 6 else "ospfd"
            self.saved = {dev: [r for r in n.routes(dev) if _is_ospf(r)]
                          for dev in n.devs if daemon in n.dyn_daemons(dev)}
        self.deadline = float(t) + self.delay_s if self.saved else None

    def due(self, t):
        return self.deadline is not None and float(t) >= self.deadline - 1e-9

    def pending(self, t):
        return self.deadline is not None and not self.due(t)

    def apply(self, current, t):
        if self.pending(t):
            return _HeldNet(current, self.saved)
        self.deadline = None
        self.saved = {}
        return current

    def note(self):
        return (f"OSPF/OSPFv3: ospf_convergence_s={self.delay_s:g} s; "
                "link up/down'da eski OSPF yo'llari saqlanadi (SPF vaqt modeli, jonli OSPF emas)")
