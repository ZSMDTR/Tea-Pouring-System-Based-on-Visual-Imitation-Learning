"""CPU-only model data transforms; run in each OpenPI environment."""
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.append(str(ROOT/'full_training'))
import multi_view_policy as policy
from multi_view_config import DualCameraDataConfig
from openpi.models import pi0_config
from openpi import transforms


class InputTests(unittest.TestCase):
    def setUp(self):
        self.obs={'state':np.arange(7,dtype=np.float32),'actions':np.arange(350,dtype=np.float32).reshape(50,7),
            'wrist_rgb':np.zeros((3,480,640),np.float32),'head_rgb':np.ones((3,480,640),np.float32),'prompt':b'task'}

    def test_distinct_views_land_in_correct_unmasked_slots(self):
        result=policy.DualCameraInputs()(self.obs)
        self.assertEqual(result['image']['base_0_rgb'].mean(),255)
        self.assertEqual(result['image']['left_wrist_0_rgb'].mean(),0)
        self.assertEqual(result['image_mask'],{'base_0_rgb':True,'left_wrist_0_rgb':True,'right_wrist_0_rgb':False})
        np.testing.assert_array_equal(result['state'],[0,1,-2,-3,-4,5,6])
        np.testing.assert_array_equal(result['actions'],self.obs['actions'])
        np.testing.assert_array_equal(self.obs['state'],np.arange(7))

    def test_missing_head_fails_instead_of_silently_masking_it(self):
        del self.obs['head_rgb']
        with self.assertRaises(ValueError):policy.DualCameraInputs()(self.obs)

    def test_delta_and_absolute_roundtrip_preserves_six_axes_and_gripper(self):
        config=DualCameraDataConfig(repo_id='local/dual_test').create(ROOT/'.runtime/dual_camera_qa/assets',pi0_config.Pi0Config(pi05=True))
        result=transforms.compose(config.data_transforms.inputs)(self.obs)
        self.assertEqual(result['actions'][0,6],self.obs['actions'][0,6])
        restored=transforms.compose(config.data_transforms.outputs)(result)
        np.testing.assert_array_equal(restored['actions'],self.obs['actions'])


if __name__=='__main__':unittest.main()
