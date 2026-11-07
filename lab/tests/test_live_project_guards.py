"""Invalid live requests cannot replace documents or reach native adapters."""

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab.drivers.base import BaseDriver, DriverError, EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.drivers.mininet_driver import MininetDriver
from lab.model import empty_project


def fixture():
    project = empty_project("liveguard")
    for i in (1, 2):
        name = f"h{i}"
        project["devices"][name] = {"id": i, "type": "host", "config": {
            "interfaces": {f"{name}-eth1": {"ip": f"10.0.0.{i}/24"}}
        }}
    project["links"]["l1"] = {
        "a": {"dev": "h1", "port": 1}, "b": {"dev": "h2", "port": 1},
        "params": {"bw": 10, "delay": "1ms", "queue": 100}, "up": True,
    }
    return project


def running_driver(kind):
    driver = kind(EventBus())
    driver.project = fixture()
    driver.started_at = 123
    if kind is MininetDriver:
        driver._links["l1"] = SimpleNamespace(intf1=Mock(params={}), intf2=Mock(params={}))
        driver._tc_opts = Mock(return_value=({"bw": 20}, True))
    return driver


class LiveProjectGuards(unittest.TestCase):
    def test_validation_uses_detached_candidate_and_preserves_previous_on_error(self):
        driver = BaseDriver(EventBus())
        previous = fixture()
        driver.project = previous
        incoming = fixture()
        incoming["name"] = "candidate"
        with patch("lab.model.validate_project", return_value=["devices: invalid"]) as validate:
            with self.assertRaisesRegex(DriverError, "devices: invalid"):
                driver._set_project(incoming)
        checked = validate.call_args.args[0]
        self.assertEqual(checked, incoming)
        self.assertIsNot(checked, incoming)
        self.assertIs(driver.project, previous)

    def test_successful_replacement_is_detached_from_caller(self):
        driver = BaseDriver(EventBus())
        incoming = fixture()
        driver._set_project(incoming)
        incoming["devices"]["h1"]["config"]["interfaces"]["h1-eth1"]["ip"] = "changed"
        self.assertEqual(driver.project["devices"]["h1"]["config"]["interfaces"]["h1-eth1"]["ip"],
                         "10.0.0.1/24")

    def test_invalid_projects_reject_before_live_operation_work_in_both_drivers(self):
        for kind in (DemoDriver, MininetDriver):
            driver = running_driver(kind)
            previous = driver.project
            incoming = fixture()
            incoming["name"] = "candidate"
            incoming["devices"]["h1"]["id"] = 0
            for operation, target in (("add_device", "h1"), ("apply_device_config", "h1"),
                                      ("add_link", "l1"), ("controller_restart", "c1"), ("p4_load", "h1")):
                with self.subTest(driver=kind.__name__, operation=operation):
                    with self.assertRaises(DriverError):
                        getattr(driver, operation)(incoming, target)
                    self.assertIs(driver.project, previous)
                    self.assertEqual(driver.started_at, 123)

    def test_missing_targets_and_wrong_p4_types_preserve_previous_document(self):
        for kind in (DemoDriver, MininetDriver):
            driver = running_driver(kind)
            previous = driver.project
            incoming = fixture()
            incoming["name"] = "candidate"
            for operation, target in (("add_device", "ghost"), ("apply_device_config", "ghost"),
                                      ("add_link", "missing"), ("controller_restart", "c1"),
                                      ("p4_load", "ghost"), ("p4_load", "h1")):
                with self.subTest(driver=kind.__name__, operation=operation, target=target):
                    with self.assertRaises(DriverError):
                        getattr(driver, operation)(incoming, target)
                    self.assertIs(driver.project, previous)

    def test_bad_up_flags_do_not_apply_otherwise_valid_link_parameters(self):
        for kind in (DemoDriver, MininetDriver):
            driver = running_driver(kind)
            before = copy.deepcopy(driver.project)
            for up in (0, 1, "false", [], {}):
                with self.subTest(driver=kind.__name__, up=up):
                    with self.assertRaisesRegex(DriverError, "boolean"):
                        driver.update_link("l1", params={"bw": 20}, up=up)
                    self.assertEqual(driver.project, before)
            if kind is MininetDriver:
                driver._tc_opts.assert_not_called()
                for intf in (driver._links["l1"].intf1, driver._links["l1"].intf2):
                    intf.config.assert_not_called()
                    intf.ifconfig.assert_not_called()
                    self.assertEqual(intf.params, {})

    def test_bad_parameters_do_not_change_link_state_or_call_native_configuration(self):
        for kind in (DemoDriver, MininetDriver):
            driver = running_driver(kind)
            before = copy.deepcopy(driver.project)
            for params in ({"bw": 0}, {"queue": float("inf")}, {"loss": float("nan")}, [1]):
                with self.subTest(driver=kind.__name__, params=params):
                    with self.assertRaises((DriverError, ValueError)):
                        driver.update_link("l1", params=params, up=False)
                    self.assertEqual(driver.project, before)
            if kind is MininetDriver:
                driver._tc_opts.assert_not_called()
                for intf in (driver._links["l1"].intf1, driver._links["l1"].intf2):
                    intf.config.assert_not_called()
                    intf.ifconfig.assert_not_called()

    def test_valid_link_update_commits_parameters_and_boolean_state(self):
        for kind in (DemoDriver, MininetDriver):
            driver = running_driver(kind)
            response = driver.update_link("l1", params={"bw": 20}, up=False)
            self.assertTrue(response["ok"])
            self.assertFalse(driver.project["links"]["l1"]["up"])
            self.assertEqual(driver.project["links"]["l1"]["params"]["bw"], 20)
            if kind is MininetDriver:
                for intf in (driver._links["l1"].intf1, driver._links["l1"].intf2):
                    intf.config.assert_called_once_with(bw=20)
                    intf.ifconfig.assert_called_once_with("down")
                    self.assertEqual(intf.params["bw"], 20)

    def test_live_config_accepts_valid_candidates_in_both_drivers(self):
        for kind in (DemoDriver, MininetDriver):
            driver = running_driver(kind)
            incoming = fixture()
            incoming["devices"]["h1"]["config"]["gateway"] = "10.0.0.254"
            if kind is MininetDriver:
                driver._config_ns_node = Mock()
                driver._write_ctrl_configs = Mock()
            self.assertEqual(driver.apply_device_config(incoming, "h1"), {"ok": True})
            self.assertEqual(driver.project, incoming)
            self.assertIsNot(driver.project, incoming)
            if kind is MininetDriver:
                driver._config_ns_node.assert_called_once_with("h1")


if __name__ == "__main__":
    unittest.main()
