"""Model providers. The service depends only on providers.base.Provider."""

from recipe_qa.config import Settings
from recipe_qa.providers.base import Provider


def make_provider(settings: Settings) -> Provider:
    if settings.provider == "fake":
        from recipe_qa.providers.fake import FakeProvider

        return FakeProvider()
    from recipe_qa.providers.claude import ClaudeProvider

    return ClaudeProvider(settings.model, settings.provider_timeout_s)
