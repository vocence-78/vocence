"""
Audio processing utilities for Vocence.

Provides functions for audio manipulation using ffmpeg including
segment extraction, clipping, and waveform analysis.
"""

import os
import base64
import asyncio
import subprocess
from typing import Any, Dict, List


class FFmpegError(Exception):
    """Raised when an ffmpeg operation fails."""
    pass


async def extract_audio_segments(audio_path: str, output_dir: str, max_segments: int = 6) -> List[str]:
    """Extract audio segments from an audio file for GPT-4o analysis.
    
    Args:
        audio_path: Path to the input audio file
        output_dir: Directory to save extracted segments
        max_segments: Maximum number of segments to extract
        
    Returns:
        List of paths to extracted segment files
        
    Raises:
        FFmpegError: If ffmpeg fails to extract segments
    """
    os.makedirs(output_dir, exist_ok=True)
    # Clear existing segments
    for f in os.listdir(output_dir):
        os.remove(os.path.join(output_dir, f))
    
    # Extract segments at regular intervals
    duration = await get_audio_duration(audio_path)
    if duration == 0:
        raise FFmpegError("Cannot determine audio duration")
    
    segment_duration = duration / max_segments
    segment_paths = []
    
    for i in range(max_segments):
        start_time = i * segment_duration
        output_path = os.path.join(output_dir, f"segment_{i:02d}.wav")
        
        result = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-i", audio_path, "-ss", str(start_time),
             "-t", str(segment_duration), "-ar", "22050", "-ac", "1",
             "-q:a", "2", output_path],
            capture_output=True,
        )
        if result.returncode != 0:
            raise FFmpegError(f"Failed to extract segment: {result.stderr.decode()[:500]}")
        
        if os.path.exists(output_path):
            segment_paths.append(output_path)
    
    return sorted(segment_paths)


def segments_to_base64(segment_paths: List[str]) -> List[Dict[str, Any]]:
    """Convert audio segment files to base64 content for OpenAI.
    
    Args:
        segment_paths: List of paths to audio segment files
        
    Returns:
        List of OpenAI-compatible audio content dictionaries
    """
    content = []
    for path in segment_paths:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content.append({
            "type": "input_audio",
            "input_audio": {"data": f"data:audio/wav;base64,{b64}"},
        })
    return content


async def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe.
    
    Args:
        audio_path: Path to the audio file
        
    Returns:
        Duration in seconds, or 0.0 if unable to determine
    """
    result = await asyncio.to_thread(
        subprocess.run,
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


async def extract_audio_clip(audio_path: str, output_path: str, start_offset: float, duration: float) -> None:
    """Extract a clip from an audio file using ffmpeg.
    
    Args:
        audio_path: Path to the input audio file
        output_path: Path for the output clip
        start_offset: Start time in seconds
        duration: Clip duration in seconds
        
    Raises:
        FFmpegError: If ffmpeg fails to extract clip
    """
    result = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-ss", str(start_offset), "-i", audio_path,
         "-t", str(duration), "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", output_path],
        capture_output=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"Failed to extract clip: {result.stderr.decode()[:500]}")


async def extract_first_segment(audio_path: str, output_path: str, start_offset: float = 0, duration: float = 1.0) -> None:
    """Extract the first segment from an audio file.
    
    Args:
        audio_path: Path to the input audio file
        output_path: Path for the output segment
        start_offset: Start time offset in seconds
        duration: Segment duration in seconds
        
    Raises:
        FFmpegError: If ffmpeg fails to extract segment
    """
    result = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-ss", str(start_offset), "-i", audio_path,
         "-t", str(duration), "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", output_path],
        capture_output=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"Failed to extract first segment: {result.stderr.decode()[:500]}")


async def combine_audio_side_by_side(left_path: str, right_path: str, output_path: str) -> None:
    """Combine two audio files side-by-side for comparison (stereo).
    
    Args:
        left_path: Path to the left audio
        right_path: Path to the right audio
        output_path: Path for the combined output audio
        
    Raises:
        FFmpegError: If ffmpeg fails to combine audio
    """
    result = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-i", left_path, "-i", right_path,
         "-filter_complex", "[0:a][1:a]amerge=inputs=2[aout]",
         "-map", "[aout]", "-ar", "22050", "-c:a", "pcm_s16le", output_path],
        capture_output=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"Failed to combine audio: {result.stderr.decode()[:500]}")

