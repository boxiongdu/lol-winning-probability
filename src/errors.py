"""Errors whose authored messages are safe to show to a command-line user."""


class ConfigurationError(ValueError):
    """Configuration is missing or invalid; messages must never include its value."""


class InputError(ValueError):
    """A local input has an invalid shape; messages must not include file contents."""
