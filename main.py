import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yt_dlp 
from yt_dlp.utils import DownloadError

SUPPORTED_BITRATES = [64, 128, 192, 256, 320]
DEFAULT_OUTPUT_DIR = os.path.join(str(Path.home()), "Downloads", "YouTube MP3")


def find_ffmpeg_executable() -> str | None:
    env_candidates = [
        os.environ.get("FFMPEG_PATH"),
        os.environ.get("FFMPEG_BINARY"),
    ]

    for candidate in env_candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    for command_name in ("ffmpeg", "ffmpeg.exe"):
        resolved = shutil.which(command_name)
        if resolved:
            return resolved

    if os.name == "nt":
        common_windows_paths = [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            os.path.join(os.path.expanduser("~"), "scoop", "shims", "ffmpeg.exe"),
            os.path.join(
                os.path.expanduser("~"),
                "AppData",
                "Local",
                "Microsoft",
                "WinGet",
                "Links",
                "ffmpeg.exe",
            ),
        ]
        for candidate in common_windows_paths:
            if os.path.isfile(candidate):
                return candidate

    return None


def ensure_ffmpeg() -> str:
    ffmpeg_executable = find_ffmpeg_executable()
    if ffmpeg_executable is None:
        raise RuntimeError(
            "FFmpeg is not installed or not visible to this Python process. "
            "Add ffmpeg to PATH or set FFMPEG_PATH to ffmpeg.exe."
        )

    try:
        subprocess.run(
            [ffmpeg_executable, "-version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        raise RuntimeError(
            f"FFmpeg was found at '{ffmpeg_executable}', but could not be executed."
        ) from exc

    return ffmpeg_executable


def prompt_for_url() -> str:
    while True:
        url = input("Enter the YouTube URL: ").strip()
        if url:
            return url
        print("The URL cannot be empty. Please enter a valid YouTube link.")


def prompt_for_bitrate() -> int:
    while True:
        value = input(
            f"Choose bitrate in kbps ({', '.join(map(str, SUPPORTED_BITRATES))}): "
        ).strip()

        if not value:
            print("Bitrate cannot be empty.")
            continue

        try:
            bitrate = int(value)
        except ValueError:
            print("Invalid bitrate. Please enter a number.")
            continue

        if bitrate not in SUPPORTED_BITRATES:
            print(f"Unsupported bitrate. Use one of: {SUPPORTED_BITRATES}")
            continue

        return bitrate


def prompt_for_output_dir() -> str:
    default_dir = DEFAULT_OUTPUT_DIR
    value = input(f"Enter output folder (press Enter for default: {default_dir}): ").strip()
    if value:
        return value
    return default_dir


def _resolve_downloaded_file(info: dict, ydl: yt_dlp.YoutubeDL, temp_dir: str) -> str | None:
    requested_downloads = info.get("requested_downloads") or []
    for item in requested_downloads:
        path = item.get("filepath")
        if path and os.path.isfile(path):
            return path

    filename = info.get("_filename")
    if filename and os.path.isfile(filename):
        return filename

    try:
        prepared = ydl.prepare_filename(info)
        if prepared and os.path.isfile(prepared):
            return prepared
    except Exception:
        pass

    candidates = [p for p in Path(temp_dir).iterdir() if p.is_file()]
    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(candidates[0])

    return None


def download_audio_only(url: str, temp_dir: str) -> str:
    output_template = os.path.join(temp_dir, "%(title)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": output_template,
        "geo_bypass": True,
        "extract_flat": False,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = _resolve_downloaded_file(info, ydl, temp_dir)
    except DownloadError as exc:
        message = str(exc)
        if "HTTP Error 403" in message:
            raise RuntimeError(
                "YouTube blocked this request (HTTP 403). "
                "Update yt-dlp, then retry. If it still fails, pass browser cookies with yt-dlp."
            ) from exc
        raise RuntimeError(message) from exc

    if not downloaded_file:
        raise FileNotFoundError(f"No audio file was downloaded for: {url}")

    return downloaded_file


def convert_to_mp3(
    ffmpeg_executable: str, input_file: str, output_file: str, bitrate_kbps: int
) -> None:
    subprocess.run(
        [
            ffmpeg_executable,
            "-y",
            "-i",
            input_file,
            "-vn",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            output_file,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def download_mp3(url: str, bitrate_kbps: int, output_dir: str) -> str:
    if bitrate_kbps not in SUPPORTED_BITRATES:
        raise ValueError(f"Unsupported bitrate: {bitrate_kbps}. Use: {SUPPORTED_BITRATES}")

    ffmpeg_executable = ensure_ffmpeg()
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="yt_mp3_")

    try:
        downloaded_file = download_audio_only(url, temp_dir)
        file_name = Path(downloaded_file).stem
        final_output = os.path.join(output_dir, f"{file_name}.mp3")

        if os.path.exists(final_output):
            base_name = Path(final_output).stem
            counter = 1
            while True:
                candidate = os.path.join(output_dir, f"{base_name}_{counter}.mp3")
                if not os.path.exists(candidate):
                    final_output = candidate
                    break
                counter += 1

        convert_to_mp3(ffmpeg_executable, downloaded_file, final_output, bitrate_kbps)
        return final_output
    finally:
        for child in Path(temp_dir).iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


def run_download_session() -> None:
    print("YouTube to MP3 Console Downloader")
    print("Supported bitrates:", SUPPORTED_BITRATES)
    print("-" * 50)

    while True:
        url = prompt_for_url()
        bitrate = prompt_for_bitrate()
        output_dir = prompt_for_output_dir()

        try:
            file_path = download_mp3(url, bitrate, output_dir)
            print(f"Success! MP3 saved to: {file_path}")
        except Exception as exc:
            print(f"Error: {exc}")

        again = input("Download another file? (y/n): ").strip().lower()
        if again not in {"y", "yes"}:
            print("Goodbye.")
            break


if __name__ == "__main__":
    run_download_session()
