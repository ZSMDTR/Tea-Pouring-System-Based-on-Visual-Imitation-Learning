import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from device_overview import overview

class DeviceOverviewTests(unittest.TestCase):
    def test_configured_serial_and_calibration_presence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);dev=root/'dev';dev.mkdir();(dev/'ttyUSB0').touch();usb=root/'usb';camera=usb/'1-2';camera.mkdir(parents=True);(camera/'product').write_text('Orbbec Gemini 305');(camera/'serial').write_text('wrist-1')
            profile=root/'profile.json';profile.write_text(json.dumps({'leader_port':str(dev/'ttyUSB0'),'leader_id':'leader','robot_id':'follower','camera':{'serial_number_or_name':'wrist-1'}}))
            calibration=root/'calibration';p=calibration/'robots/seeed_b601_rs_follower/follower.json';p.parent.mkdir(parents=True);p.write_text('{}')
            data=overview(profile,{'ready':False},{'active':False},dev=dev,usb=usb,calibration=calibration)
            self.assertTrue(data['leader_present']);self.assertEqual(data['wrist']['serial'],'wrist-1');self.assertEqual(len(data['cameras']),1)
            self.assertFalse(data['calibrations'][0]['exists']);self.assertTrue(data['calibrations'][1]['exists']);self.assertFalse(data['can']['ready'])
            profile_data=json.loads(profile.read_text());profile_data['camera']['serial_number_or_name']='missing';profile.write_text(json.dumps(profile_data))
            self.assertIsNone(overview(profile,{}, {},dev=dev,usb=usb,calibration=calibration)['wrist'])
if __name__=='__main__':unittest.main()
