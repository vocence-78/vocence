"""
Vocence - Voice Intelligence Subnet on Bittensor

A subnet for the development and evaluation of voice intelligence models
(PromptTTS, STT, STS, voice cloning, and related capabilities). The current
implementation (Q1) focuses on PromptTTS; validators evaluate miner outputs
using content correctness, audio quality, and prompt adherence.
"""

from importlib.metadata import version

__version__ = version("vocence")

