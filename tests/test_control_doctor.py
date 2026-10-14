import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/doctor_control.py"
spec = importlib.util.spec_from_file_location("doctor_control", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class ControlDoctorTests(unittest.TestCase):
    def test_usb_pcan_is_a_candidate(self):
        device = {"name": "PCAN-USB", "vid_pid": "VID_0C72&PID_000C"}
        self.assertEqual(doctor.can_candidates([device]), [device])

    def test_bluetooth_and_speaker_are_not_can(self):
        devices = [{"name": "Bluetooth Serial COM9", "vid_pid": ""},
                   {"name": "Awinic Smart Amplifier Driver for Speakers", "vid_pid": ""},
                   {"name": "USB speaker", "vid_pid": "VID_1234&PID_5678"}]
        self.assertEqual(doctor.can_candidates(devices), [])

    def test_unknown_usb_is_not_assumed_compatible(self):
        device = {"name": "USB Serial CH340", "vid_pid": "VID_1A86&PID_7523"}
        self.assertEqual(doctor.can_candidates([device]), [])

    def test_device_without_friendly_name_does_not_break_detection(self):
        device = {"name": None, "vid_pid": "VID_1234&PID_5678", "status": "Unknown"}
        self.assertEqual(doctor.can_candidates([device]), [])

    def test_linux_filters_ethernet_and_loopback(self):
        links = [{"ifname": "eth0", "link_type": "ether"},
                 {"ifname": "lo", "link_type": "loopback"},
                 {"ifname": "can0", "link_type": "can"}]
        self.assertEqual(doctor.linux_can_links(links), [links[2]])

    def test_linux_requires_up_and_correct_bitrate(self):
        link = {"flags": ["UP"], "linkinfo": {"info_data": {
            "bittiming": {"bitrate": 1000000}}}}
        self.assertTrue(doctor.linux_can_ready(link))
        link["flags"] = []
        self.assertFalse(doctor.linux_can_ready(link))
        link["flags"] = ["UP"]
        link["linkinfo"]["info_data"]["bittiming"]["bitrate"] = 500000
        self.assertFalse(doctor.linux_can_ready(link))
