class InvestReporterError(Exception):
    """Base class for all application errors."""


class InvalidTickerError(InvestReporterError, ValueError):
    """Raised when a ticker does not look like a valid B3 ticker."""


class ProviderError(InvestReporterError):
    """Raised when an external data provider fails or returns unusable data."""


class ConfigurationError(InvestReporterError):
    """Raised when required settings (e.g. API credentials) are missing or invalid."""


class SubscriptionError(InvestReporterError, ValueError):
    """Raised when a sign-up form is invalid (bad email, no stock chosen...)."""
