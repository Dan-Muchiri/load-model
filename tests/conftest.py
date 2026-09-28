import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schema import REFERENCE_HOUSEHOLD  # noqa: E402


@pytest.fixture
def household():
    """A fresh copy of the reference household with CSI fixed (no NASA data needed)."""
    h = copy.deepcopy(REFERENCE_HOUSEHOLD)
    h["model_parameters"]["csi_source"] = "fixed"
    return h


@pytest.fixture
def empty_household(household):
    """Reference household with every appliance and bulb switched off."""
    for a in household["appliances"]:
        a["count"] = 0
    for b in household["bulbs"]:
        b["count"] = 0
    return household
