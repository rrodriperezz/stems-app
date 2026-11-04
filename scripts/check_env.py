"""Check the environment: Python, torch, GPU, demucs, ffmpeg. Read-only, changes nothing."""
import platform
import shutil
import subprocess
import sys


def main() -> None:
    print(f"Python      : {sys.version.split()[0]} ({sys.executable})")
    print(f"OS          : {platform.system()} {platform.release()}")

    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path:
            out = subprocess.run([tool, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
            print(f"{tool:<12}: OK — {out}")
        else:
            print(f"{tool:<12}: NOT FOUND")

    try:
        import torch
        print(f"torch       : {torch.__version__}")
        cuda = torch.cuda.is_available()
        print(f"GPU (cuda/rocm available): {cuda}")
        if cuda:
            print(f"  device 0  : {torch.cuda.get_device_name(0)}")
            # Real smoke test: a matmul on the GPU. Catches backends that "show up" but crash.
            try:
                a = torch.rand(512, 512, device="cuda")
                (a @ a).sum().item()
                print("  smoke test: OK (GPU matmul works)")
            except Exception as e:
                print(f"  smoke test: FAILED — {e}")
    except ImportError as e:
        print(f"torch       : NOT INSTALLED — {e}")

    try:
        import demucs
        print(f"demucs      : {demucs.__version__}")
    except ImportError as e:
        print(f"demucs      : NOT INSTALLED — {e}")


if __name__ == "__main__":
    main()
