#!/usr/bin/env python3
"""
지식 베이스(관리자 동영상 업로드)용으로 로컬 mp4 등을 ffmpeg 로 재인코딩합니다.

목적은 **파일 크기·처리 시간**을 줄여 Gemini 업로드·요약 타임아웃을 피하기 쉽게 만드는 것입니다.

사전 준비: 시스템에 ``ffmpeg`` 와 ``ffprobe`` 가 설치되어 ``PATH`` 에 있어야 합니다.
(보통 ffmpeg 패키지에 ffprobe 가 함께 들어 있습니다.)

맥(macOS · Homebrew)::

    brew install ffmpeg

윈도우(Chocolatey 예시)::

    choco install ffmpeg -y

사용 예(프로젝트 폴더에서)::

    python3 compress_video_for_kb.py "강의원본.mp4"
    python3 compress_video_for_kb.py "강의원본.mp4" -o "uploads/강의_압축.mp4" --height 540 --crf 28
    python3 compress_video_for_kb.py "강의원본.mp4" --target-mb 80
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def find_ffmpeg() -> str | None:
    """
    실행 파일 ``ffmpeg`` 의 경로를 찾습니다(없으면 None).

    Returns:
        PATH 상의 ffmpeg 전체 경로 문자열, 없으면 None.
    """
    return shutil.which("ffmpeg")


def find_ffprobe() -> str | None:
    """
    실행 파일 ``ffprobe`` 의 경로를 찾습니다(없으면 None).

    Returns:
        PATH 상의 ffprobe 전체 경로 문자열, 없으면 None.
    """
    return shutil.which("ffprobe")


def default_output_path(src: Path) -> Path:
    """
    입력 파일 옆에 ``_kb`` 접미사가 붙은 mp4 경로를 만듭니다.

    Args:
        src: 원본 동영상 경로.

    Returns:
        ``원본_stem_kb720p.mp4`` 형태의 경로(같은 디렉터리).
    """
    return src.with_name(f"{src.stem}_kb720p.mp4")


def probe_duration_seconds(ffprobe_bin: str, src: Path) -> float:
    """
    ffprobe 로 컨테이너 기준 재생 시간(초)을 읽습니다.

    Args:
        ffprobe_bin: ffprobe 실행 파일 경로.
        src: 입력 동영상.

    Returns:
        0 초보다 큰 실수(초).

    Raises:
        RuntimeError: 길이를 읽지 못한 경우.
    """
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(src),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "ffprobe 실패")
    raw = (proc.stdout or "").strip()
    try:
        sec = float(raw)
    except ValueError as e:
        raise RuntimeError(f"ffprobe duration 파싱 실패: {raw!r}") from e
    if not math.isfinite(sec) or sec <= 0:
        raise RuntimeError(f"잘못된 재생 길이: {sec}")
    return sec


def video_bitrate_kbps_for_target_size(
    *,
    target_mb: float,
    duration_sec: float,
    audio_kbps: int,
    mux_overhead: float = 0.03,
) -> int:
    """
    목표 파일 크기(MiB 기준)에 맞추기 위한 **평균 영상 비트레이트(kbps)** 를 계산합니다.

    용기(mp4) 오버헤드·오디오 트랙을 빼고 남은 예산을 영상 비트레이트로 나눕니다.
    실제 파일은 인코딩·장면에 따라 목표보다 조금 크거나 작을 수 있습니다.

    Args:
        target_mb: 목표 파일 크기(MiB, 1024*1024 바이트 기준으로 환산).
        duration_sec: 영상 길이(초).
        audio_kbps: 오디오 AAC 평균 비트레이트(kbps).
        mux_overhead: 컨테이너·기타 여유(비율). 기본 3%.

    Returns:
        libx264 ``-b:v`` 에 쓸 정수 kbps(최소 80kbps 캡).

    Raises:
        ValueError: 목표가 너무 작아 음수 예산이 되는 경우.
    """
    if target_mb <= 0 or duration_sec <= 0:
        raise ValueError("target_mb 와 duration_sec 는 양수여야 합니다.")
    target_bytes = target_mb * 1024.0 * 1024.0
    usable_bytes = target_bytes * (1.0 - mux_overhead)
    audio_bps = (audio_kbps * 1000) / 8.0
    audio_bytes_total = audio_bps * duration_sec
    video_bytes_total = usable_bytes - audio_bytes_total
    if video_bytes_total <= 0:
        raise ValueError(
            "목표 용량이 너무 작습니다. --target-mb 를 키우거나 --audio-kbps 를 낮추거나 영상을 더 짧게 잘라 주세요."
        )
    video_bps = (video_bytes_total * 8.0) / duration_sec
    kbps = int(video_bps / 1000.0)
    return max(80, min(kbps, 50_000))


def build_ffmpeg_crf_command(
    *,
    ffmpeg_bin: str,
    src: Path,
    dst: Path,
    height: int,
    crf: int,
    preset: str,
    audio_kbps: int,
) -> list[str]:
    """
    H.264+AAC 로 CRF 모드 한 번에 인코딩하는 ffmpeg 인자 목록을 만듭니다.

    Args:
        ffmpeg_bin: ffmpeg 실행 파일 경로.
        src: 입력 파일.
        dst: 출력 mp4 경로.
        height: 세로 해상도.
        crf: libx264 CRF.
        preset: x264 preset.
        audio_kbps: AAC 평균 비트레이트(kbps). 강하게 줄이면 전체 파일 크기를 더 깎습니다.

    Returns:
        ``subprocess.run`` 에 넘길 argv 리스트.
    """
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale=-2:{height}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        f"{audio_kbps}k",
        "-movflags",
        "+faststart",
        str(dst),
    ]


def build_ffmpeg_pass_command(
    *,
    ffmpeg_bin: str,
    src: Path,
    dst: Path | None,
    height: int,
    preset: str,
    video_kbps: int,
    audio_kbps: int,
    pass_index: int,
    passlogfile_prefix: str,
) -> list[str]:
    """
    libx264 2-pass 인코딩용 ffmpeg 인자 한 번(pass1 또는 pass2)을 만듭니다.

    Args:
        ffmpeg_bin: ffmpeg 경로.
        src: 입력.
        dst: 출력 mp4(pass2에서만). pass1에서는 None이면 null 로 버림.
        height: 세로 해상도.
        preset: x264 preset.
        video_kbps: 평균 영상 비트레이트(kbps).
        audio_kbps: AAC 비트레이트(kbps).
        pass_index: 1 또는 2.
        passlogfile_prefix: -passlogfile 에 넣을 접두 경로(확장자 없음).

    Returns:
        argv 리스트.
    """
    vf = f"scale=-2:{height}"
    br = f"{max(80, video_kbps)}k"
    cmd: list[str] = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-b:v",
        br,
        "-pass",
        str(pass_index),
        "-passlogfile",
        passlogfile_prefix,
    ]
    if pass_index == 1:
        cmd += ["-an", "-f", "mp4"]
        if sys.platform == "win32":
            cmd.append("NUL")
        else:
            cmd.append("/dev/null")
    else:
        cmd += [
            "-c:a",
            "aac",
            "-b:a",
            f"{audio_kbps}k",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    return cmd


def run_ffmpeg(cmd: list[str]) -> None:
    """
    ffmpeg 를 실행하고 실패 시 표준 에러를 보여 줍니다.

    Args:
        cmd: argv.

    Raises:
        SystemExit: ffmpeg 가 0 이 아닌 코드로 끝난 경우.
    """
    print("실행:", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout or "(stderr 없음)\n")
        raise SystemExit(proc.returncode)


def cleanup_pass_logs(prefix_path: Path) -> None:
    """
    x264 가 남긴 2-pass 로그 ``접두-0.log`` ``접두-1.log`` 등을 지웁니다.

    Args:
        prefix_path: ``-passlogfile`` 에 넘긴 경로 문자열과 동일 stem 을 가진 Path.
    """
    base = str(prefix_path)
    for suffix in ("-0.log", "-1.log", "-0.log.mbtree", "-1.log.mbtree"):
        p = Path(base + suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    """CLI 진입점입니다."""
    parser = argparse.ArgumentParser(
        description="지식 베이스 업로드용 동영상 용량 줄이기(ffmpeg H.264+AAC)",
    )
    parser.add_argument("input", type=Path, help="입력 동영상 경로")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="출력 mp4 경로(미지정 시 입력 옆에 _kb720p.mp4)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=720,
        help="세로 해상도(기본 720)",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default="medium",
        help="x264 preset(기본 medium)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--crf",
        type=int,
        default=None,
        help="CRF 한 번 인코딩(기본 26). --target-mb 와 함께 쓸 수 없습니다.",
    )
    mode.add_argument(
        "--target-mb",
        type=float,
        default=None,
        help="목표 파일 크기(MiB, 약 MB). 두 패스(bitrate)로 근사합니다.",
    )
    parser.add_argument(
        "--audio-kbps",
        type=int,
        default=96,
        help="AAC 비트레이트(kbps). CRF·target-mb 모두에 적용되며 target-mb 예산에도 반영됩니다. 기본 96",
    )
    args = parser.parse_args()
    target_mb = args.target_mb
    use_crf = target_mb is None
    crf = args.crf if args.crf is not None else 26

    ff = find_ffmpeg()
    if not ff:
        print(
            "ffmpeg 를 찾을 수 없습니다. macOS: brew install ffmpeg / Windows: PATH 에 ffmpeg 등록 후 다시 시도하세요.",
            file=sys.stderr,
        )
        raise SystemExit(127)

    src = args.input.expanduser().resolve()
    if not src.is_file():
        print(f"입력 파일이 없습니다: {src}", file=sys.stderr)
        raise SystemExit(2)

    dst = (args.output.expanduser().resolve() if args.output else default_output_path(src))
    dst.parent.mkdir(parents=True, exist_ok=True)

    height = max(144, min(args.height, 2160))

    before = src.stat().st_size

    if use_crf:
        crf_clamped = max(18, min(crf, 32))
        audio_kb = max(32, min(args.audio_kbps, 320))
        cmd = build_ffmpeg_crf_command(
            ffmpeg_bin=ff,
            src=src,
            dst=dst,
            height=height,
            crf=crf_clamped,
            preset=args.preset,
            audio_kbps=audio_kb,
        )
        run_ffmpeg(cmd)
    else:
        fb = find_ffprobe()
        if not fb:
            print(
                "ffprobe 가 필요합니다(--target-mb). ffmpeg 와 함께 설치되어 있는지 확인하세요.",
                file=sys.stderr,
            )
            raise SystemExit(127)
        try:
            duration = probe_duration_seconds(fb, src)
            v_kbps = video_bitrate_kbps_for_target_size(
                target_mb=target_mb,
                duration_sec=duration,
                audio_kbps=max(32, min(args.audio_kbps, 320)),
            )
        except (RuntimeError, ValueError) as e:
            print(str(e), file=sys.stderr)
            raise SystemExit(1) from e

        audio_kbps = max(32, min(args.audio_kbps, 320))
        print(
            f"목표 약 {target_mb:.2f} MiB · 길이 {duration:.1f}s · 영상평균 ~{v_kbps} kbps · 오디오 {audio_kbps} kbps (2-pass)",
            flush=True,
        )
        uid = uuid.uuid4().hex[:12]
        pass_prefix_path = dst.parent / f".ff2pass_{dst.stem}_{uid}"
        pass_prefix = str(pass_prefix_path)
        try:
            run_ffmpeg(
                build_ffmpeg_pass_command(
                    ffmpeg_bin=ff,
                    src=src,
                    dst=None,
                    height=height,
                    preset=args.preset,
                    video_kbps=v_kbps,
                    audio_kbps=audio_kbps,
                    pass_index=1,
                    passlogfile_prefix=pass_prefix,
                ),
            )
            run_ffmpeg(
                build_ffmpeg_pass_command(
                    ffmpeg_bin=ff,
                    src=src,
                    dst=dst,
                    height=height,
                    preset=args.preset,
                    video_kbps=v_kbps,
                    audio_kbps=audio_kbps,
                    pass_index=2,
                    passlogfile_prefix=pass_prefix,
                ),
            )
        finally:
            cleanup_pass_logs(pass_prefix_path)

    after = dst.stat().st_size
    ratio = (after / before * 100.0) if before else 0.0
    print(f"완료: {dst}", flush=True)
    print(
        f"크기: {before / (1024*1024):.2f} MiB → {after / (1024*1024):.2f} MiB (원본 대비 약 {ratio:.1f}%)",
        flush=True,
    )
    if target_mb is not None:
        goal = target_mb * 1024 * 1024
        if after > goal * 1.12:
            print(
                "참고: 목표보다 크게 나왔습니다. --height 을 더 낮추거나 --audio-kbps 를 줄이거나 목표치를 소폭 올려 다시 실행해 보세요.",
                flush=True,
            )
        elif after < goal * 0.88:
            print("참고: 목표보다 작습니다(장면이 단순하면 흔한 현상입니다).", flush=True)
    print("이제 관리자 화면에서 이 파일을 업로드하면 됩니다.", flush=True)


if __name__ == "__main__":
    main()
