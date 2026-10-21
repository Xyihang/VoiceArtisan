"""引擎适配器：每个适配器封装一个可独立安装/卸载的 TTS 引擎。"""
from .base import BaseAdapter, EngineAdapter, ProgressFn, Reporter

__all__ = ["BaseAdapter", "EngineAdapter", "ProgressFn", "Reporter"]
