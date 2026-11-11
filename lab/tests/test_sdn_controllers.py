import unittest
from lab.model import validate_project, empty_project
from lab.project_shape import validate_shape, project_for_execution
from controller import find_pox, start_controller


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


if __name__ == "__main__":
    unittest.main()
