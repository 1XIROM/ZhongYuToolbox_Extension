#!/usr/bin/env python3
"""兼容入口：真实实现已迁到 tools/answer-pipeline/src/answer_pipeline。

保留这个文件，是为了让既有调用（定时任务、文档里的
`python scripts/pipeline.py ...`）一行都不用改。子命令与参数完全一致。

区别只有一处：本项目根由脚本位置决定（见下方 ANSWER_ROOT），
所以即使从别的目录调用也能定位 input/output。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "answer-pipeline" / "src"
sys.path.insert(0, str(SRC))
os.environ.setdefault("ANSWER_ROOT", str(ROOT))

from answer_pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
