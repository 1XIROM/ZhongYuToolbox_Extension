"""目录与版式配置。

所有路径都从外部解析而不是写死，方便这套管线作为别人的一个子模块嵌进去：
优先级是 命令行参数 > 环境变量 > 从当前目录向上探测 > 包所在位置向上探测。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Optional

ENV_ROOT = "ANSWER_ROOT"
ENV_INPUT = "ANSWER_INPUT"
ENV_OUTPUT = "ANSWER_OUTPUT"
ENV_STATE = "ANSWER_STATE"

_MARKERS = ("input", ".workbuddy/auto")


def find_root(start: Optional[Path] = None) -> Path:
    """向上找项目根：优先含 input/ 的目录，其次含 .workbuddy/auto 的目录。

    标记故意取 `.workbuddy/auto` 而不是 `.workbuddy`：用户主目录里也有
    `.workbuddy`（那是 WorkBuddy 自身的配置），用它当标记会让本包在
    临时目录里跑时误认主目录为项目根。
    都找不到就返回起点，不做"跳去包里那个项目"的兜底。
    """
    here = Path(start or Path.cwd()).resolve()
    for marker in _MARKERS:
        for p in [here, *here.parents]:
            if (p / marker).exists():
                return p
    return here


@dataclass
class Layout:
    """版式参数。改这里就能换字号/页边距，不必动 CSS 字符串。"""

    page_size: str = "A4 landscape"
    page_margin: str = "12mm 16mm 10mm"
    body_pt: float = 11.0
    answer_pt: float = 12.0
    analysis_pt: float = 14.0
    heading_pt: float = 13.0
    subheading_pt: float = 12.0
    quote_pt: float = 9.5
    line_height: float = 1.7
    doc_width: str = "297mm"
    doc_min_height: str = "210mm"
    page_bg: str = "#1c1f26"
    paper_bg: str = "#fff"


@dataclass
class Config:
    root: Path
    input_dir: Path
    output_dir: Path
    state_dir: Path
    layout: Layout = field(default_factory=Layout)
    lock_timeout: float = 30.0
    settle_seconds: float = 2.0
    chrome_path: Optional[str] = None
    katex_dir: Optional[Path] = None

    @property
    def drafts(self) -> Path:
        return self.state_dir / "drafts"

    @property
    def staging(self) -> Path:
        return self.state_dir / "staging"

    @property
    def queue(self) -> Path:
        return self.state_dir / "queue"

    @property
    def manifest(self) -> Path:
        return self.state_dir / "manifest.json"

    @property
    def logs(self) -> Path:
        return self.state_dir / "logs"

    @property
    def latex_cache(self) -> Path:
        return self.state_dir / "latex"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / ".lock"

    def ensure_dirs(self) -> None:
        for d in (self.input_dir, self.output_dir, self.state_dir,
                  self.drafts, self.staging, self.queue, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    def rel(self, p: Path) -> str:
        """把绝对路径转成相对 root 的 posix 路径；不在 root 内则原样返回。"""
        try:
            return Path(p).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    def abs(self, s: Optional[str]) -> Optional[Path]:
        if not s:
            return None
        p = Path(s)
        return p if p.is_absolute() else (self.root / p)

    @classmethod
    def build(
        cls,
        root: Optional[str] = None,
        input_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        state_dir: Optional[str] = None,
        config_file: Optional[str] = None,
    ) -> "Config":
        base = Path(root).resolve() if root else (
            Path(os.environ[ENV_ROOT]).resolve() if os.environ.get(ENV_ROOT) else find_root()
        )
        inp = Path(input_dir).resolve() if input_dir else (
            Path(os.environ[ENV_INPUT]).resolve() if os.environ.get(ENV_INPUT) else base / "input"
        )
        out = Path(output_dir).resolve() if output_dir else (
            Path(os.environ[ENV_OUTPUT]).resolve() if os.environ.get(ENV_OUTPUT) else base / "output"
        )
        st = Path(state_dir).resolve() if state_dir else (
            Path(os.environ[ENV_STATE]).resolve() if os.environ.get(ENV_STATE)
            else base / ".workbuddy" / "auto"
        )
        layout = Layout()
        cfg = cls(root=base, input_dir=inp, output_dir=out, state_dir=st, layout=layout)
        cfg._load_config_file(config_file)
        return cfg

    def _load_config_file(self, config_file: Optional[str]) -> None:
        if not config_file:
            default = self.root / "answer-pipeline.json"
            config_file = str(default) if default.exists() else None
        if not config_file:
            return
        p = Path(config_file)
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在：{p}")
        data: Dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        layout_data = data.get("layout") or {}
        known = asdict(self.layout)
        for k, v in layout_data.items():
            if k not in known:
                raise KeyError(f"未知的版式字段：{k}")
            setattr(self.layout, k, v)
        for k in ("lock_timeout", "settle_seconds"):
            if k in data:
                setattr(self, k, data[k])
        if data.get("chrome_path"):
            self.chrome_path = data["chrome_path"]
        if data.get("katex_dir"):
            self.katex_dir = Path(data["katex_dir"])
