"""同一次分析共享模型等待预算；网络失败后本轮不再重复等待模型。"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import os
import time
import requests


def _setting(name, default, maximum):
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if 1 <= value <= maximum else default


BUDGET_SECONDS = _setting('NETTUNE_LLM_BUDGET_SECONDS', 30., 60.)
READ_SECONDS = _setting('NETTUNE_LLM_READ_SECONDS', 10., 20.)


@dataclass
class Budget:
    deadline: float
    transport_failed: bool = False


_current = ContextVar('nettune_llm_budget', default=None)


@contextmanager
def request_budget(seconds=None):
    """嵌套工具沿用外层预算，不因再次进入研判函数而重新计时。"""
    if _current.get() is not None:
        yield
        return
    token = _current.set(Budget(time.monotonic() + (BUDGET_SECONDS if seconds is None else seconds)))
    try:
        yield
    finally:
        _current.reset(token)


def with_request_budget(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with request_budget():
            return fn(*args, **kwargs)
    return wrapped


def network_timeouts():
    """requests 使用连接/读取超时；剩余预算不足时不再发新请求。"""
    budget = _current.get()
    if budget is not None and budget.transport_failed:
        raise requests.Timeout('本次分析的模型连接已失败，使用已有工具结果')
    remaining = budget.deadline - time.monotonic() if budget is not None else READ_SECONDS + 2
    if remaining <= .5:
        raise requests.Timeout('本次分析的模型等待预算已用尽，使用已有工具结果')
    connect = min(2., remaining / 3)
    read = min(READ_SECONDS, remaining - connect)
    return (connect, read)


def transport_failed():
    budget = _current.get()
    if budget is not None:
        budget.transport_failed = True
