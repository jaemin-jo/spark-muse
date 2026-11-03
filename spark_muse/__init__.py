"""Spark Muse: Spark's batched browser-agent loop, open source, running on Meta Muse."""
from .agent import Result, run
from .model import Muse, Provider

__all__ = ['Muse', 'Provider', 'Result', 'run']
__version__ = '0.1.0'
