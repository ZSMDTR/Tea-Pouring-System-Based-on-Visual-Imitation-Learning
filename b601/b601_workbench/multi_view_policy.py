"""Opt-in B601 dual RGB input; preserve tested joint/action transforms."""
import dataclasses
import numpy as np
try:
    from openpi.policies.b601_policy import B601Inputs, B601Outputs, _parse_image
except ModuleNotFoundError:
    # The isolated official runtime uses the existing local B601 adapter.
    from b601_policy import B601Inputs, B601Outputs, _parse_image


@dataclasses.dataclass(frozen=True)
class DualCameraInputs(B601Inputs):
    def __call__(self, data):
        inputs = super().__call__(data)
        if 'head_rgb' not in data:
            raise ValueError('双视角模型缺少 head_rgb；拒绝使用空白画面代替。')
        head = _parse_image(data['head_rgb'])
        if head.shape != inputs['image']['left_wrist_0_rgb'].shape:
            raise ValueError('腕部与第三视角的模型输入尺寸不一致。')
        inputs['image']['base_0_rgb'] = head
        inputs['image_mask']['base_0_rgb'] = np.True_
        return inputs
