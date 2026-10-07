"""Refuse unsafe targets before pytest imports any fixture/business module."""

import os

from scripts.test_environment import assert_safe_test_environment


assert_safe_test_environment(os.environ)
