import shutil
import subprocess


class FfmpegError(RuntimeError):
    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"ffmpeg exited with {returncode}: {stderr[-4000:]}")


def ffmpeg_binary() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError("ffmpeg not found on PATH")
    return path


def run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_binary(), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(result.returncode, result.stderr)
