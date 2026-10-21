"""torch 守卫与依赖探测的离线自检（不联网、不装包、不改环境）。

直接运行：`python backend/tests/test_torch_guard.py`
覆盖三件事：
1. `deps.probe_packages()` 会把 `pkg[extra]` / `pkg==1.2.3` 归一化成裸包名并给出真实状态；
2. `torchguard.assert_unchanged()` 在 torch/CUDA 变化时抛可读的 TORCH_CHANGED（含恢复命令）；
3. `installer.pip_install()` 的命令行确实带 `--no-deps` / `-c <constraints>` / `--no-cache-dir`，
   并且安装后守卫生效（用打桩的 subprocess + 假快照模拟「pip 把 torch 换掉了」）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

for _stream in (sys.stdout, sys.stderr):  # GBK 终端下中文/✓ 不抛 UnicodeEncodeError
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

from app.engine.adapters import deps, installer, torchguard  # noqa: E402
from app.errors import AppError  # noqa: E402

PASSED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} 失败 {detail}")
    PASSED.append(label)
    print(f"  ✓ {label}{('：' + detail) if detail else ''}")


def test_probe_normalization() -> None:
    print("[1] 依赖探测：约束写法归一化 + 真实状态")
    statuses = deps.probe_packages(["soundfile[extra]", "onnxruntime==1.23.2", "voxcpm", "not-a-real-pkg-xyz"])
    names = [item["name"] for item in statuses]
    check("归一化裸包名", names == ["soundfile", "onnxruntime", "voxcpm", "not-a-real-pkg-xyz"], str(names))
    check(
        "返回四字段",
        all(set(item) == {"name", "installed", "version", "import_name"} for item in statuses),
    )
    voxcpm = next(item for item in statuses if item["name"] == "voxcpm")
    check("voxcpm 已装且带版本", voxcpm["installed"] and bool(voxcpm["version"]), voxcpm["version"] or "")
    ghost = next(item for item in statuses if item["name"] == "not-a-real-pkg-xyz")
    check("不存在的包判为缺失", ghost["installed"] is False and ghost["version"] is None)

    deps.invalidate(["not-a-real-pkg-xyz"])
    again = deps.probe_packages(["not-a-real-pkg-xyz"])[0]
    check("invalidate() 后仍可重查", again["installed"] is False)


def test_assert_unchanged() -> None:
    print("[2] assert_unchanged：快照一致放行，变化则抛 TORCH_CHANGED")
    before = {"torch": "2.4.1+cu121", "torchaudio": "2.4.1", "torchvision": "0.19.1+cu121",
              "cuda": "12.1", "cuda_available": True}
    same = dict(before)
    current = torchguard.assert_unchanged(before, same)
    check("一致时不抛异常", current == same)

    broken = {"torch": "2.14.1+cpu", "torchaudio": "2.11.0", "torchvision": "0.19.1+cu121",
              "cuda": None, "cuda_available": False}
    try:
        torchguard.assert_unchanged(before, broken)
    except AppError as exc:
        detail = exc.detail
        check("抛出 TORCH_CHANGED", exc.code == "TORCH_CHANGED", exc.code)
        check("错误可读（含前后版本）", "2.4.1+cu121" in detail and "2.14.1+cpu" in detail, detail)
        check(
            "含恢复命令",
            "-m pip install" in detail
            and "torch==2.4.1+cu121" in detail
            and "--index-url https://download.pytorch.org/whl/cu121" in detail,
            detail,
        )
        print(f"      错误详情：{detail}")
    else:  # pragma: no cover
        raise AssertionError("torch 被改动却没有抛错")


class FakePopen:
    """假 pip 进程：可选地「装完之后把 torch 换掉」。"""

    def __init__(self, cmd, *args, **kwargs):
        self.cmd = list(cmd)
        FakePopen.last_cmd = self.cmd
        FakePopen.last_env = dict(kwargs.get("env") or {})
        FakePopen.calls.append(self.cmd)
        constraint = None
        for index, token in enumerate(self.cmd):
            if token == "-c" and index + 1 < len(self.cmd):
                constraint = Path(self.cmd[index + 1])
        FakePopen.constraint_text = constraint.read_text(encoding="utf-8") if constraint else ""
        FakePopen.constraint_path = constraint
        self.stdout = iter(["Looking in indexes: fake", "Requirement already satisfied: torch"])

    def wait(self) -> int:
        return 0

    last_cmd: list[str] = []
    calls: list[list[str]] = []
    constraint_text = ""
    constraint_path: Path | None = None
    last_env: dict[str, str] = {}


@pytest.mark.parametrize("no_deps", [True, False])
def test_pip_install_guard(no_deps: bool) -> None:
    label = "主包 --no-deps" if no_deps else "精选依赖"
    print(f"[3] pip_install 打桩（{label}）：命令行带守卫参数，安装后快照变化即中止")
    snapshots = [
        {"torch": "2.4.1+cu121", "torchaudio": "2.4.1", "torchvision": "0.19.1+cu121",
         "cuda": "12.1", "cuda_available": True},
        {"torch": "2.14.1+cpu", "torchaudio": "2.11.0", "torchvision": "0.19.1+cu121",
         "cuda": None, "cuda_available": False},
    ]
    original_popen = installer.subprocess.Popen
    original_snapshot = torchguard.snapshot
    FakePopen.calls = []
    installer.subprocess.Popen = FakePopen  # type: ignore[assignment]
    torchguard.snapshot = lambda: snapshots.pop(0)  # type: ignore[assignment]
    try:
        installer.pip_install(["voxcpm"], lambda pct, msg: None, no_deps=no_deps)
    except AppError as exc:
        check(f"{label}：抛 TORCH_CHANGED", exc.code == "TORCH_CHANGED", exc.code)
        cmd = FakePopen.last_cmd
        cache_dir = FakePopen.last_env.get("PIP_CACHE_DIR", "")
        check(
            f"{label}：pip 缓存重定向到项目 .cache",
            bool(cache_dir) and Path(cache_dir).is_absolute() and Path(cache_dir).name == "pip"
            and ".cache" in Path(cache_dir).parts,
            cache_dir,
        )
        check(f"{label}：带 -c 约束文件", "-c" in cmd and "constraints" in str(FakePopen.constraint_path))
        real_torch = torchguard.effective_version("torch")
        check(
            f"{label}：约束钉住 torch 现值",
            bool(real_torch) and f"torch=={real_torch}" in FakePopen.constraint_text,
            FakePopen.constraint_text.replace("\n", " ").strip(),
        )
        check(f"{label}：--no-deps 用法正确", ("--no-deps" in cmd) is no_deps, " ".join(cmd[:8]))
        check(f"{label}：临时约束文件已清理", not FakePopen.constraint_path.exists())
        print(f"      中止详情：{exc.detail}")
    else:  # pragma: no cover
        raise AssertionError("torch 被改动却没有中止安装")
    finally:
        installer.subprocess.Popen = original_popen  # type: ignore[assignment]
        torchguard.snapshot = original_snapshot  # type: ignore[assignment]


def test_constraints_file() -> None:
    print("[4] 约束文件：torch 家族钉在现值，装完即删")
    pins = torchguard.constraints({"numpy": "2.2.6"})
    check("含 torch 家族", all(name in pins for name in ("torch", "torchvision")), str(pins))
    path = torchguard.write_constraints_file(pins)
    text = path.read_text(encoding="utf-8")
    check("文件内容为 name==version", "torch==" in text and "numpy==2.2.6" in text)
    check(
        "恢复命令常量可用（显式解释器 + == 版本 + cu121 源）",
        "-m pip install" in torchguard.default_repair_command()
        and "torch==" in torchguard.default_repair_command()
        and "--index-url https://download.pytorch.org/whl/" in torchguard.default_repair_command(),
        torchguard.default_repair_command(),
    )
    meta = torchguard.installed_version("torch")
    effective = torchguard.effective_version("torch")
    check("版本以「真正可 import 的那份」为准", bool(effective), str(effective))
    if meta and effective and meta != effective:
        print(f"      注意：元数据 {meta} ≠ 可导入版本 {effective}（残留 dist-info），约束按 {effective} 生成")
    path.unlink(missing_ok=True)
    check("临时文件可删除", not path.exists())


def main() -> int:
    print(f"临时目录：{tempfile.gettempdir()}")
    try:
        test_probe_normalization()
        test_assert_unchanged()
        test_pip_install_guard(no_deps=True)
        test_pip_install_guard(no_deps=False)
        test_constraints_file()
    except AssertionError as exc:
        print(f"\n✗ 失败：{exc}")
        return 1
    print(f"\n全部通过：{len(PASSED)} 条断言")
    return 0


if __name__ == "__main__":
    sys.exit(main())
