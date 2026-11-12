import time
import unittest
from unittest.mock import MagicMock, patch
from lab.model import validate_project, empty_project
from lab.project_shape import validate_shape, project_for_execution
from controller import (
    find_pox,
    find_ryu,
    start_controller,
    check_controller_health,
    get_controller_guidance,
    auto_detect_controllers,
)


class SDNControllerTests(unittest.TestCase):
    def setUp(self):
        self.project = empty_project("test_controllers")

    def test_valid_controller_types(self):
        for ctype in ["ryu", "os-ken", "pox", "remote", "onos", "odl", "floodlight"]:
            p = empty_project("test")
            p["controllers"] = {"c1": {"type": ctype, "port": 6653}}
            errors = validate_project(p)
            self.assertEqual(errors, [], f"Controller type '{ctype}' should be valid: {errors}")

    def test_invalid_controller_type_rejected(self):
        p = empty_project("test")
        p["controllers"] = {"c1": {"type": "unknown_ctrl", "port": 6653}}
        errors = validate_project(p)
        self.assertTrue(any("noma'lum tur" in e for e in errors), f"Expected unknown type error: {errors}")

    def test_invalid_controller_ip_rejected(self):
        p = empty_project("test")
        p["controllers"] = {"c1": {"type": "remote", "ip": "invalid-ip-address", "port": 6653}}
        errors = validate_project(p)
        self.assertTrue(any("noto'g'ri IP manzil" in e for e in errors), f"Expected IP error: {errors}")

    def test_valid_controller_ip_accepted(self):
        p = empty_project("test")
        p["controllers"] = {"c1": {"type": "remote", "ip": "192.168.1.50", "port": 6653}}
        errors = validate_project(p)
        self.assertEqual(errors, [])

    def test_controllers_on_different_ips_same_port_dont_collide(self):
        p = empty_project("test")
        p["controllers"] = {
            "c1": {"type": "remote", "ip": "10.0.0.1", "port": 6653},
            "c2": {"type": "remote", "ip": "10.0.0.2", "port": 6653},
        }
        errors = validate_project(p)
        self.assertEqual(errors, [])

    def test_controllers_on_same_ip_same_port_collide(self):
        p = empty_project("test")
        p["controllers"] = {
            "c1": {"type": "remote", "ip": "10.0.0.1", "port": 6653},
            "c2": {"type": "remote", "ip": "10.0.0.1", "port": 6653},
        }
        errors = validate_project(p)
        self.assertTrue(any("takrorlangan" in e for e in errors))

    def test_controller_shape_validation(self):
        p = empty_project("test")
        p["controllers"] = {
            "c1": {"type": "remote", "ip": "127.0.0.1", "port": 6653, "modules": "forwarding.l2_learning", "code": ""}
        }
        self.assertEqual(validate_shape(p), [])
        prep = project_for_execution(p)
        self.assertEqual(prep["controllers"]["c1"]["type"], "remote")
        self.assertEqual(prep["controllers"]["c1"]["ip"], "127.0.0.1")

    def test_remote_controller_start_check(self):
        # Testing remote controller health check without throwing exceptions
        res = start_controller(controller_type="remote", ip="127.0.0.1", port=59999)
        self.assertIsNone(res)

    def test_pox_finder(self):
        pox_path = find_pox()
        # Since we cloned it to /home/user/pox, it should find it
        self.assertIsNotNone(pox_path)
        self.assertTrue(pox_path.endswith("pox.py"))

    def test_ryu_finder(self):
        ryu_cmd = find_ryu()
        # In this environment, os-ken / ryu is installed
        self.assertIsNotNone(ryu_cmd)

    def test_controller_health_check_graceful_timeout(self):
        # Health check on an inactive port should return False gracefully without raising exceptions
        is_healthy = check_controller_health("127.0.0.1", 59998, timeout=0.2)
        self.assertFalse(is_healthy)

    def test_controller_guidance(self):
        for ctype in ["onos", "odl", "floodlight", "pox", "ryu", "remote"]:
            guidance = get_controller_guidance(ctype)
            self.assertIsInstance(guidance, str)
            self.assertTrue(len(guidance) > 20)
            if ctype in ("onos", "odl", "floodlight"):
                self.assertIn("docker", guidance.lower())

    def test_auto_detect_controllers(self):
        detected = auto_detect_controllers(host="127.0.0.1", timeout=0.2)
        self.assertIn("installed", detected)
        self.assertIn("listening_ports", detected)
        self.assertIn("detected", detected)
        self.assertIsInstance(detected["installed"]["pox"], bool)
        self.assertIsInstance(detected["installed"]["ryu"], bool)

    @patch("network_build.warn_external_controller_telemetry")
    @patch("mininet.net.Mininet")
    def test_network_build_external_controller_flags(self, mock_mn_cls, mock_warn):
        from network_build import build_topology
        dummy_topo = {
            "switches": {},
            "hosts": {},
            "access_links": {},
            "links": {},
        }
        net = build_topology(dummy_topo, controller_type="onos", controller_ip="10.1.2.3", controller_port=6653)
        self.assertTrue(net.is_external_controller)
        self.assertEqual(net.controller_type, "onos")
        self.assertEqual(net.controller_ip, "10.1.2.3")
        self.assertEqual(net.controller_port, 6653)
        mock_warn.assert_called_once_with("onos")

    def test_driver_remote_controller_lifecycle(self):
        from lab.drivers.mininet_driver import MininetDriver
        drv = MininetDriver(bus=MagicMock())
        p = empty_project("test_drv")
        p["controllers"] = {"c1": {"type": "remote", "ip": "127.0.0.1", "port": 59999}}
        drv.project = p
        drv.started_at = time.time()
        res = drv.controller_restart(p, "c1")
        self.assertTrue(res["ok"])
        self.assertIn("c1", res["log"])
        self.assertIn("ULANIB BO'LMADI", res["log"])
        drv._teardown()
        self.assertIsNone(drv.project)
        self.assertIsNone(drv.started_at)


if __name__ == "__main__":
    unittest.main()
