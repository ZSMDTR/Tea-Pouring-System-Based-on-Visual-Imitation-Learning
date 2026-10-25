import dataclasses

import einops
import numpy as np

from openpi import transforms
from openpi.models import model as _model


# B601 dataset convention:
#
# action:
#   leader / policy coordinate convention
#
# observation.state:
#   follower / motor coordinate convention
#
# Real B601 demonstration data verified the first six joint relations:
#
#   shoulder_pan   +1
#   shoulder_lift  +1
#   elbow_flex     -1
#   wrist_flex     -1
#   wrist_yaw      -1
#   wrist_roll     +1
#
# Gripper is intentionally NOT converted here yet. It is excluded from
# DeltaActions and remains an absolute action dimension.
_STATE_TO_POLICY_SIGN = np.asarray(
    [1.0, 1.0, -1.0, -1.0, -1.0, 1.0],
    dtype=np.float32,
)


def _parse_image(image) -> np.ndarray:
    """Convert a LeRobot image to uint8 HWC format expected by OpenPI."""
    image = np.asarray(image)

    # LeRobot training images are commonly float32 in [0, 1].
    if np.issubdtype(image.dtype, np.floating):
        image = (255 * image).astype(np.uint8)

    # LeRobot commonly exposes images as CHW during training.
    if image.ndim == 3 and image.shape[0] == 3:
        image = einops.rearrange(image, "c h w -> h w c")

    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(
            f"Expected B601 RGB image in CHW or HWC format, got {image.shape}"
        )

    return image


@dataclasses.dataclass(frozen=True)
class B601Inputs(transforms.DataTransformFn):
    """Convert B601 observations/actions into OpenPI model inputs."""

    model_type: _model.ModelType = _model.ModelType.PI05

    def __call__(self, data: dict) -> dict:
        if self.model_type not in (_model.ModelType.PI0, _model.ModelType.PI05):
            raise ValueError(f"Unsupported model type for B601: {self.model_type}")

        # Raw B601 follower state is 7D:
        # [6 arm joints, gripper]
        state = np.asarray(data["state"], dtype=np.float32).copy()

        if state.shape[-1] != 7:
            raise ValueError(
                f"Expected B601 state dimension 7, got shape {state.shape}"
            )

        # Convert ONLY the six arm joints from follower/motor convention
        # into the leader/action/policy convention.
        #
        # This is required before DeltaActions performs:
        #     relative_action = action - state
        state[..., :6] *= _STATE_TO_POLICY_SIGN

        wrist_image = _parse_image(data["wrist_rgb"])

        # π0 / π0.5 always expects three image slots.
        #
        # B601 v1 uses only the wrist RGB camera.
        # Missing base/right-wrist views are zero-filled and masked out.
        zero_image = np.zeros_like(wrist_image)

        inputs = {
            "state": state,
            "image": {
                "base_0_rgb": zero_image,
                "left_wrist_0_rgb": wrist_image,
                "right_wrist_0_rgb": zero_image.copy(),
            },
            "image_mask": {
                "base_0_rgb": np.False_,
                "left_wrist_0_rgb": np.True_,
                "right_wrist_0_rgb": np.False_,
            },
        }

        # Dataset actions are already in leader/policy convention.
        # Do NOT sign-flip them.
        if "actions" in data:
            actions = np.asarray(data["actions"], dtype=np.float32).copy()

            if actions.shape[-1] != 7:
                raise ValueError(
                    f"Expected B601 action dimension 7, got shape {actions.shape}"
                )

            inputs["actions"] = actions

        if "prompt" in data:
            prompt = data["prompt"]
            if isinstance(prompt, bytes):
                prompt = prompt.decode("utf-8")
            inputs["prompt"] = prompt

        return inputs


@dataclasses.dataclass(frozen=True)
class B601Outputs(transforms.DataTransformFn):
    """Convert OpenPI outputs back to the B601 7D policy action space."""

    def __call__(self, data: dict) -> dict:
        # OpenPI π0.5 predicts 32 action dimensions internally.
        # B601 uses only the first seven.
        #
        # IMPORTANT:
        # Do NOT apply the joint sign conversion here.
        #
        # After AbsoluteActions, these seven values are already back in
        # B601's leader/policy action convention, which is exactly the
        # convention stored in the LeRobot dataset's `action` field.
        return {
            "actions": np.asarray(data["actions"])[..., :7],
        }
