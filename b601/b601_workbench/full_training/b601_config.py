import dataclasses
import pathlib
from collections.abc import Sequence
from typing_extensions import override
from openpi.training.config import DataConfigFactory,DataConfig,ModelTransformFactory
from openpi import transforms as _transforms
from openpi.models import model as _model
import b601_policy

@dataclasses.dataclass(frozen=True)
class LeRobotB601DataConfig(DataConfigFactory):
    """OpenPI data configuration for the Seeed reBot B601-RS."""

    # Convert the six arm joints to state-relative actions.
    # The seventh dimension (gripper) remains absolute.
    use_delta_joint_actions: bool = True

    # Optional fallback prompt.
    default_prompt: str | None = None

    # The raw LeRobot dataset stores actions under "action".
    action_sequence_keys: Sequence[str] = ("action",)

    @override
    def create(
        self,
        assets_dirs: pathlib.Path,
        model_config: _model.BaseModelConfig,
    ) -> DataConfig:
        # Dataset-only key remapping.
        #
        # observation.images.orbbec_color
        #     -> wrist_rgb
        #
        # observation.state
        #     -> state
        #
        # action
        #     -> actions
        #
        # LeRobot task
        #     -> prompt
        repack_transform = _transforms.Group(
            inputs=[
                _transforms.RepackTransform(
                    {
                        "wrist_rgb": "observation.images.wrist_rgb",
                        "state": "observation.state",
                        "actions": "action",
                        "prompt": "prompt",
                    }
                )
            ]
        )

        # Robot-specific transforms shared by training and inference.
        data_transforms = _transforms.Group(
            inputs=[
                b601_policy.B601Inputs(
                    model_type=model_config.model_type,
                )
            ],
            outputs=[
                b601_policy.B601Outputs(),
            ],
        )

        # Six arm joints are relative to the current policy-space state.
        # Gripper stays absolute.
        if self.use_delta_joint_actions:
            delta_action_mask = _transforms.make_bool_mask(6, -1)

            data_transforms = data_transforms.push(
                inputs=[
                    _transforms.DeltaActions(delta_action_mask),
                ],
                outputs=[
                    _transforms.AbsoluteActions(delta_action_mask),
                ],
            )

        # π0.5-specific transforms:
        # resize -> tokenize prompt/state -> pad 7D to 32D.
        model_transforms = ModelTransformFactory(
            default_prompt=self.default_prompt
        )(model_config)

        return dataclasses.replace(
            self.create_base_config(assets_dirs, model_config),
            repack_transforms=repack_transform,
            data_transforms=data_transforms,
            model_transforms=model_transforms,
            action_sequence_keys=self.action_sequence_keys,
        )
