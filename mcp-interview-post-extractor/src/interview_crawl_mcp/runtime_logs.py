"""把服务进程的标准输出和标准错误同时写到终端与每次运行的日志文件。"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class TeeStream:
    def __init__(self, terminal, log_file, lock: threading.RLock):
        self.terminal = terminal
        self.log_file = log_file
        self.lock = lock

    def write(self, text: str) -> int:
        with self.lock:
            self.terminal.write(text)
            self.log_file.write(ANSI_ESCAPE.sub("", text))
            self.log_file.flush()
        return len(text)

    def flush(self) -> None:
        with self.lock:
            self.terminal.flush()
            self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.terminal, name)


def install_output_log(log_dir: Path) -> Path:
    """启动时安装一次；之后的 print、日志及未捕获异常都会同步落盘。"""

    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}.log"
    log_file = path.open("x", encoding="utf-8", buffering=1)
    lock = threading.RLock()
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = TeeStream(original_stdout, log_file, lock)
    sys.stderr = TeeStream(original_stderr, log_file, lock)

    # 已创建的标准 logging handler 可能仍引用原始流，也需要接入副本。
    loggers = [logging.getLogger()]
    loggers.extend(
        item for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    )
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                if handler.stream is original_stdout:
                    handler.setStream(sys.stdout)
                elif handler.stream is original_stderr:
                    handler.setStream(sys.stderr)
    return path
