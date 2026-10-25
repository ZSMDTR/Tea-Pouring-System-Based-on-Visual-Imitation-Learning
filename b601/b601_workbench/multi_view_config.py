"""Separate dual-view data factory for both existing OpenPI environments."""
import dataclasses
from openpi.training.config import DataConfigFactory, ModelTransformFactory
from openpi import transforms as t
from multi_view_policy import DualCameraInputs, B601Outputs


@dataclasses.dataclass(frozen=True)
class DualCameraDataConfig(DataConfigFactory):
    use_delta_joint_actions: bool = True
    default_prompt: str | None = None
    action_sequence_keys: tuple[str, ...] = ('action',)

    def create(self, assets_dirs, model_config):
        repack = t.Group(inputs=[t.RepackTransform({
            'wrist_rgb': 'observation.images.wrist_rgb',
            'head_rgb': 'observation.images.head_rgb',
            'state': 'observation.state', 'actions': 'action', 'prompt': 'prompt'})])
        transforms = t.Group(inputs=[DualCameraInputs(model_type=model_config.model_type)], outputs=[B601Outputs()])
        if self.use_delta_joint_actions:
            mask = t.make_bool_mask(6, -1)
            transforms = transforms.push(inputs=[t.DeltaActions(mask)], outputs=[t.AbsoluteActions(mask)])
        return dataclasses.replace(self.create_base_config(assets_dirs, model_config),
            repack_transforms=repack, data_transforms=transforms,
            model_transforms=ModelTransformFactory(default_prompt=self.default_prompt)(model_config),
            action_sequence_keys=self.action_sequence_keys)


def with_dual_cameras(config):
    previous = config.data
    return dataclasses.replace(config, data=DualCameraDataConfig(
        repo_id=previous.repo_id, assets=previous.assets, base_config=previous.base_config,
        use_delta_joint_actions=previous.use_delta_joint_actions, default_prompt=previous.default_prompt))
