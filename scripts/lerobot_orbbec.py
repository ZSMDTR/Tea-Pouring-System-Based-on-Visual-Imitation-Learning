"""Load this project's Orbbec camera plugin, then run a pinned LeRobot command."""
import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("record", "teleoperate"):
        print("Usage: bash scripts/camera.sh lerobot record|teleoperate [LeRobot options]")
        return 2
    import rebot_tea.lerobot_orbbec  # noqa: F401 -- registers the camera config before draccus.
    from importlib import import_module
    action = sys.argv.pop(1)
    module = import_module(f"lerobot.scripts.lerobot_{action}")
    module.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
