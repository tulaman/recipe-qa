"""Parse the `time` field of {{Recipe summary}} into total minutes."""

from recipe_qa.corpus import Fact


def parse_total_minutes(raw: str | None) -> Fact[int]:
    raise NotImplementedError
