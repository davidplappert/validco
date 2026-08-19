"""The controller contract.

Controllers are deliberately thin. Each one parses its inputs, asks a model or a
service to do the work, and serialises the answer. If a controller starts
computing something, that computation belongs in a model.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..datasets.registry import REGISTRY, DatasetRegistry
from ..http.request import Request
from ..http.response import Response

LOG = logging.getLogger(__name__)


class Controller(ABC):
    """One endpoint."""

    def __init__(self, registry: DatasetRegistry = REGISTRY):
        """Bind a dataset registry, defaulting to the process-wide one.

        Injectable so a test can point at a fixture directory instead of the
        shipped datasets.
        """
        self.registry = registry

    @abstractmethod
    def handle(self, request: Request) -> Response:
        """Produce a response for one request."""
