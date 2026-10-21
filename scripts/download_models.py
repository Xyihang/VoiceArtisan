"""模型下载脚本（ModelScope 国内直连，支持并发、断点续传、完整性校验）。

默认下载 1.7B 三件套（语音生成 / 声音克隆 / 音色设计，约 12.6GB）。

用法：
    python scripts/download_models.py                  # 下载 1.7B 三件套到 <项目>/models
    python scripts/download_models.py --links          # 只打印直链与目标目录
    python scripts/download_models.py --verify         # 校验已下载目录完整性
    python scripts/download_models.py --size 0.6B      # 仅 0.6B 回退模型（默认不启用降级）
    python scripts/download_models.py --models base --out E:/models --jobs 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPOS = {
    "1.7B": {
        "base": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "custom_voice": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "voice_design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    },
    "0.6B": {
        "base": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "custom_voice": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    },
}

MS_API = "https://www.modelscope.cn/api/v1/models"
MS_FILE = "https://www.modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"
MS_RESOLVE = "https://www.modelscope.cn/models/{repo}/resolve/master/{path}"
HF_FILE = "https://hf-mirror.com/{repo}/resolve/main/{path}"
SKIP_SUFFIX = (".md", ".gitattributes", ".jpg", ".png")
UA = {"User-Agent": "qwentts-downloader/1.1"}


def _http_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_files(repo: str) -> list[tuple[str, int]]:
    data = _http_json(f"{MS_API}/{repo}/repo/files?Revision=master&Recursive=True")
    return [
        (f["Path"], int(f.get("Size") or 0))
        for f in data["Data"]["Files"]
        if f.get("Type", "blob") != "tree"
    ]


class Progress:
    def __init__(self, total_bytes: int, label: str) -> None:
        self.total = max(1, total_bytes)
        self.label = label
        self.done = 0
        self.started = time.time()
        self._lock = threading.Lock()
        self._last_print = 0.0

    def add(self, n: int) -> None:
        with self._lock:
            self.done += n
            now = time.time()
            if now - self._last_print >= 5:
                self._last_print = now
                elapsed = max(0.1, now - self.started)
                speed = self.done / elapsed / 1024**2
                pct = min(100.0, self.done / self.total * 100)
                print(
                    f"    {self.label}: {self.done / 1024**3:.2f}/{self.total / 1024**3:.2f} GB "
                    f"({pct:.1f}%) {speed:.1f} MB/s",
                    flush=True,
                )

    def finish(self) -> None:
        elapsed = max(0.1, time.time() - self.started)
        print(
            f"    {self.label}: 完成 {self.done / 1024**3:.2f} GB，用时 {elapsed / 60:.1f} 分钟"
            f"（均速 {self.done / elapsed / 1024**2:.1f} MB/s）",
            flush=True,
        )


def download_file(url: str, target: Path, expected: int, progress: Progress, retries: int = 5) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if expected and target.exists() and target.stat().st_size == expected:
        progress.add(expected)
        return
    tmp = target.with_suffix(target.suffix + ".part")
    for attempt in range(1, retries + 1):
        resume = tmp.stat().st_size if tmp.exists() else 0
        headers = dict(UA)
        if resume:
            headers["Range"] = f"bytes={resume}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = getattr(resp, "status", 200)
                if resume and status != 206:
                    resume = 0  # 服务器不支持断点，重头下
                mode = "ab" if resume else "wb"
                with open(tmp, mode) as fh:
                    if resume:
                        progress.add(resume)
                    while True:
                        block = resp.read(1024 * 512)
                        if not block:
                            break
                        fh.write(block)
                        progress.add(len(block))
            if expected and tmp.stat().st_size != expected:
                raise IOError(f"大小不一致 {tmp.stat().st_size} != {expected}")
            tmp.replace(target)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"    重试（{attempt}/{retries}）{target.name}: {exc}", flush=True)
            if attempt == retries:
                raise
            time.sleep(min(15, 2 * attempt))


def download_repo(repo: str, out_dir: Path, source: str = "modelscope", jobs: int = 3) -> bool:
    print(f"\n== {repo} -> {out_dir} ==", flush=True)
    files = [(p, s) for p, s in list_files(repo) if not p.endswith(SKIP_SUFFIX)]
    total = sum(size for _, size in files)
    print(f"   {len(files)} 个文件，共 {total / 1024**3:.2f} GB", flush=True)
    progress = Progress(total, repo.split("/")[-1])
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {}
        for path, size in sorted(files, key=lambda x: -x[1]):
            url = (
                HF_FILE.format(repo=repo, path=urllib.parse.quote(path))
                if source == "hf"
                else MS_FILE.format(repo=repo, path=urllib.parse.quote(path))
            )
            futures[pool.submit(download_file, url, out_dir / path, size, progress)] = path
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{futures[future]}: {exc}")
    progress.finish()
    if failures:
        print("   以下文件下载失败：", flush=True)
        for item in failures:
            print("    -", item, flush=True)
        return False
    return True


def verify_repo(repo: str, out_dir: Path) -> tuple[bool, list[str]]:
    if not out_dir.exists():
        return False, [f"目录不存在：{out_dir}"]
    problems: list[str] = []
    for path, size in list_files(repo):
        if path.endswith(SKIP_SUFFIX):
            continue
        target = out_dir / path
        if not target.exists():
            problems.append(f"缺失 {path}")
        elif size and target.stat().st_size != size:
            problems.append(f"大小异常 {path}（{target.stat().st_size} != {size}）")
    return not problems, problems


def print_links(out_root: Path, size_key: str | None = None) -> None:
    sizes = [size_key] if size_key else list(REPOS)
    for size in sizes:
        for kind, repo in REPOS[size].items():
            print(f"\n### {size} {kind} -> {out_root / repo.split('/')[-1]}")
            for path, fsize in list_files(repo):
                if path.endswith(SKIP_SUFFIX):
                    continue
                print(f"  [{fsize / 1024**2:.1f} MB] {MS_RESOLVE.format(repo=repo, path=path)}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="下载 Qwen3-TTS 模型（ModelScope 直连）")
    parser.add_argument("--size", default="1.7B", choices=["1.7B", "0.6B", "all"])
    parser.add_argument("--models", nargs="*", default=None, help="base custom_voice voice_design")
    parser.add_argument("--out", default=str(root / "models"))
    parser.add_argument("--source", default="modelscope", choices=["modelscope", "hf"])
    parser.add_argument("--jobs", type=int, default=3, help="每个仓库的并发下载数")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--links", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    out_root = Path(args.out)
    sizes = ["1.7B", "0.6B"] if args.size == "all" else [args.size]
    if args.links:
        print_links(out_root, None if args.size == "all" else args.size)
        return 0
    if args.list:
        for size, repos in REPOS.items():
            for kind, repo in repos.items():
                print(f"{size}\t{kind}\t{repo}")
        return 0
    if args.verify:
        ok_all = True
        for size in sizes:
            for kind, repo in REPOS[size].items():
                ok, problems = verify_repo(repo, out_root / repo.split("/")[-1])
                print(f"[{'OK' if ok else 'FAIL'}] {size} {kind}: {out_root / repo.split('/')[-1]}")
                for p in problems:
                    print("   -", p)
                ok_all = ok_all and ok
        return 0 if ok_all else 1

    if out_root.drive and out_root.drive.upper() == os.environ.get("SystemDrive", "C:").upper():
        print(f"提示：模型目录位于系统盘 {out_root}，建议改用其它盘（--out）。")

    failures: list[str] = []
    for size in sizes:
        repos = REPOS[size]
        for kind in (args.models or list(repos.keys())):
            repo = repos.get(kind)
            if not repo:
                print(f"跳过：{size} 没有 {kind}（音色设计仅提供 1.7B）")
                continue
            name = repo.split("/")[-1]
            try:
                if not download_repo(repo, out_root / name, args.source, args.jobs):
                    failures.append(name)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: {exc}")
                print(f"   失败：{exc}")

    print("\n== 校验 ==")
    for size in sizes:
        for kind, repo in REPOS[size].items():
            if args.models and kind not in args.models:
                continue
            ok, problems = verify_repo(repo, out_root / repo.split("/")[-1])
            print(f"[{'OK' if ok else 'FAIL'}] {size} {kind} -> {out_root / repo.split('/')[-1]}")
            for p in problems[:6]:
                print("   -", p)

    if failures:
        print("\n以下仓库未完成，可重新运行本脚本继续（已下载文件会跳过/续传）：")
        for f in failures:
            print(" -", f)
        return 1
    print("\n全部完成。下一步：cd backend && python -m app.main --selfcheck")
    return 0


if __name__ == "__main__":
    sys.exit(main())
