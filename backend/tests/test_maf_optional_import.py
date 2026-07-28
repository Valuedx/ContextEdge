import subprocess
import sys


def test_base_contextedge_import_does_not_require_maf():
    script = """
import builtins

original_import = builtins.__import__

def without_maf(name, *args, **kwargs):
    if name == "agent_framework" or name.startswith("agent_framework."):
        raise ImportError("MAF intentionally unavailable")
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_maf
import contextedge
import contextedge.graph.agent
from contextedge.integrations.maf.client import HttpContextGraphClient
print(HttpContextGraphClient.__name__)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HttpContextGraphClient"
