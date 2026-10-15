"""Start both registered project kernels and execute a bounded GPU check."""
import argparse
import json
import os
from pathlib import Path

from jupyter_client import KernelManager

ROOT = Path(__file__).resolve().parents[1]


def check_kernel(name):
    manager = KernelManager(kernel_name=name)
    client = None
    result = {"kernel": name, "passed": False}
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    code = '''import json, sys, torch, numpy, cv2, pyorbbecsdk
assert torch.cuda.is_available(), "CUDA unavailable"
x = torch.ones((8, 8), device="cuda")
assert bool(torch.isfinite(x @ x).all())
torch.cuda.synchronize()
print("REBOT_KERNEL=" + json.dumps({"python": sys.executable,
    "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
    "sdk_native_config": pyorbbecsdk.Config() is not None}))
'''
    try:
        manager.start_kernel(cwd=str(ROOT), env=environment)
        client = manager.blocking_client()
        client.start_channels()
        client.wait_for_ready(timeout=30)
        streams = []

        def output(message):
            if message["msg_type"] == "stream":
                streams.append(message["content"]["text"])

        reply = client.execute_interactive(code, timeout=45, output_hook=output)
        if reply["content"]["status"] != "ok":
            raise RuntimeError(str(reply["content"]))
        lines = "".join(streams).splitlines()
        payload = next(line.split("=", 1)[1] for line in lines
                       if line.startswith("REBOT_KERNEL="))
        result.update(json.loads(payload), passed=True)
    except Exception as error:
        result["error"] = str(error)
    finally:
        if client is not None:
            client.stop_channels()
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/notebook-check.json")
    args = parser.parse_args()
    checks = [check_kernel(name) for name in ("rebot-tea", "rebot-training")]
    report = {"passed": all(item["passed"] for item in checks), "kernels": checks,
              "motor_commands_sent": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
