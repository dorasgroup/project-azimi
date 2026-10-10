"""Pure endpoint and subprocess collection rejection; never connect to services."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.test_environment import assert_safe_test_environment


def isolated():
    return {
        "DATABASE_URL": "postgresql+asyncpg://phase8:test-only@rasad-phase8-pg-LetbR0:5432/phase89_release",
        "REDIS_URL": "redis://rasad-phase8-redis-LetbR0:6379/8",
        "CELERY_BROKER_URL": "redis://rasad-phase8-redis-LetbR0:6379/8",
        "CELERY_RESULT_BACKEND": "redis://rasad-phase8-redis-LetbR0:6379/9",
    }


def test_explicit_dedicated_targets_are_accepted():
    assert_safe_test_environment(isolated())


def test_e2e_has_separate_broker_mapping_and_cannot_mix_with_regression():
    env = isolated()
    env["DATABASE_URL"] = env["DATABASE_URL"].replace("phase89_release", "phase9_e2e")
    with pytest.raises(RuntimeError):
        assert_safe_test_environment(env)
    env.update(REDIS_URL="redis://rasad-phase8-redis-LetbR0:6379/10", CELERY_BROKER_URL="redis://rasad-phase8-redis-LetbR0:6379/10", CELERY_RESULT_BACKEND="redis://rasad-phase8-redis-LetbR0:6379/11")
    assert_safe_test_environment(env)
    env["DATABASE_URL"] = env["DATABASE_URL"].replace("phase9_e2e", "phase89_release")
    with pytest.raises(RuntimeError):
        assert_safe_test_environment(env)


@pytest.mark.parametrize("key,value", [
    ("DATABASE_URL", "postgresql+asyncpg://rasad:SENSITIVE@postgres:5432/rasad"),
    ("DATABASE_URL", "postgresql+asyncpg://phase8:SENSITIVE@5.10.249.72:5432/phase89_release"),
    ("DATABASE_URL", "postgresql+asyncpg://phase8:SENSITIVE@rasad-phase8-pg-LetbR0:5432/rasad"),
    ("DATABASE_URL", "postgresql+asyncpg://phase8:SENSITIVE@rasad-phase8-pg-LetbR0:5432/phase89_release?host=postgres"),
    ("DATABASE_URL", "postgresql+asyncpg://phase8:SENSITIVE@rasad-phase8-pg-LetbR0:invalid/phase89_release"),
    ("DATABASE_URL", "postgresql+asyncpg://phase8:SENSITIVE@rasad-phase8-pg-LetbR0:5432/phase89_release#production"),
    ("REDIS_URL", "redis://rasad-redis:6379/0"),
    ("CELERY_BROKER_URL", "redis://5.10.249.72:6379/8"),
    ("CELERY_BROKER_URL", "redis://rasad-phase8-redis-LetbR0:6379/8?host=rasad-redis"),
    ("CELERY_RESULT_BACKEND", "redis://rasad-redis:6379/1"),
    ("CELERY_RESULT_BACKEND", "redis://rasad-phase8-redis-LetbR0:6379/1"),
    ("PHASE8A3_DB_URL", "postgresql+asyncpg://rasad:SENSITIVE@postgres:5432/rasad"),
    ("PHASE8A3_REDIS_URL", "redis://rasad-redis:6379/0"),
])
def test_production_and_ambiguous_targets_refused_without_secret_echo(key, value):
    env = isolated()
    env[key] = value
    with pytest.raises(RuntimeError) as error:
        assert_safe_test_environment(env)
    assert "SENSITIVE" not in str(error.value)


@pytest.mark.parametrize("missing", ["DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"])
def test_missing_explicit_setting_cannot_fall_back_to_dotenv(missing):
    env = isolated()
    del env[missing]
    with pytest.raises(RuntimeError):
        assert_safe_test_environment(env)


def test_pytest_refuses_production_before_collecting_db_fixtures():
    env = dict(os.environ)
    env.update(isolated())
    env["DATABASE_URL"] = "postgresql+asyncpg://rasad:SENSITIVE@postgres:5432/rasad"
    env.pop("PHASE8A3_DB_URL", None)
    env.pop("PHASE8A3_REDIS_URL", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_phase5_distributed_fanout.py"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Unsafe test database target refused before IO" in output
    assert "SENSITIVE" not in output
    assert "tests collected" not in output


@pytest.mark.parametrize("module", [
    "verify_database", "verify_report_exports", "verify_security", "verify_observability",
    "verify_mvp", "verify_professional_features", "verify_performance", "verify_monitoring_flow",
    "phase8_crash_worker", "phase8_recovery_probe", "phase8a3_seed", "phase9_e2e",
])
def test_direct_fixture_helpers_refuse_production_before_app_imports(module):
    env = dict(os.environ)
    env.update(isolated())
    env["DATABASE_URL"] = "postgresql+asyncpg://rasad:SENSITIVE@postgres:5432/rasad"
    env.pop("PHASE8A3_DB_URL", None)
    env.pop("PHASE8A3_REDIS_URL", None)
    result = subprocess.run([sys.executable, "-m", f"scripts.{module}"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Unsafe test database target refused before IO" in output
    assert "SENSITIVE" not in output
