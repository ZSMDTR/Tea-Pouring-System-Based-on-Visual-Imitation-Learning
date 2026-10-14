"""No SDK import, CAN connection, motor enable, or hardware commands."""
from .planning import Step


class MockRobot:
    def __init__(self):
        self.history: list[Step] = []

    def execute(self, steps: list[Step]) -> None:
        self.history.extend(steps)
