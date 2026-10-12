from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Step:
    stepNumber: int
    action: str
    data: str = ""
    expectedResult: str = ""


@dataclass(frozen=True)
class TestCase:
    jiraKey: str
    steps: list[Step]

    def to_dict(self):
        return asdict(self)
