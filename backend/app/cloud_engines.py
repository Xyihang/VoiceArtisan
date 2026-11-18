"""云端引擎远程管理：本机后端通过 SSH 调云端的 `engines_cli`。

设计取舍（见 docs/spec/CLOUD-ENGINES-1.md）：
- 走 SSH 而不是云端 HTTP：服务没起来时也能装模型（正是「先装模型再起服务」的顺序），
  而且「装了哪些模型」本来就该按云端文件系统算，直传上去的模型也能被正确识别。
- 只做文件级管理（列表 / 安装 / 删除 / 切换当前引擎），不做显存与健康探测。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

from . import cloud_hosts
from .errors import AppError
from .events import bus

logger = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"^\[\s*(?P<phase>[^\]]+?)\s*\]\s*(?P<pct>\d+)%\s*(?P<msg>.*)$")
_ENV_PREFIX = "HF_ENDPOINT=https://hf-mirror.com HF_HUB_ENABLE_HF_TRANSFER=0"


def _clean(text: str) -> str:
    from .cloud_agent import redact

    return redact(text)


class CloudEngines:
    """一个本地后端管理一台云端主机的引擎。"""

    def __init__(self, cfg_manager) -> None:
        self.cfg_manager = cfg_manager
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._jobs: dict[str, dict] = {}
        self._current: str = ""

    # ---------------- 基础设施 ----------------
    def _data_dir(self) -> Path:
        return Path(self.cfg_manager.app.data_dir)

    def _job_dir(self) -> Path:
        d = self._data_dir() / "cloud-engines" / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def profile(self, decrypt: bool = False) -> dict:
        data = cloud_hosts.load(self._data_dir(), decrypt=decrypt)
        if not data or not data.get("host"):
            raise AppError(
                "CLOUD_HOST_MISSING",
                "还没有云端主机档案：先在「云端连接 → 一键接入云主机」完成接入，或手工填写主机信息",
            )
        return data

    def _open(self):
        from .cloud_agent import _SSH

        p = self.profile(decrypt=True)
        ssh = _SSH(p["host"], int(p["ssh_port"]), p["username"], p.get("password", ""))
        ssh.connect(15.0)
        return ssh, p

    def _python_for(self, ssh, p: dict) -> str:
        """优先用部署自带的 venv 解释器：一键接入会把依赖装进 <project>/.venv，
        而系统 python 可能缺 pydantic-settings 等包，用错解释器会直接 import 失败。"""
        project = p["project_dir"]
        try:
            rc, out = ssh.run(
                f"if [ -x {project}/.venv/bin/python ]; then echo VENV; "
                f"elif [ -x {project}/.venv/bin/python3 ]; then echo VENV3; else echo SYS; fi",
                timeout=20,
            )
        except Exception:  # noqa: BLE001
            return p["python"]
        tag = (out.strip().splitlines() or ["SYS"])[-1]
        if tag == "VENV":
            return f"{project}/.venv/bin/python"
        if tag == "VENV3":
            return f"{project}/.venv/bin/python3"
        return p["python"]

    def _base_cmd(self, p: dict, args: str, python: str | None = None) -> str:
        return (
            f"cd {p['project_dir']}/backend && PYTHONPATH={p['project_dir']}/backend "
            f"{_ENV_PREFIX} {python or p['python']} -m app.engines_cli {args}"
        )

    # ---------------- 只读 ----------------
    def test(self) -> dict:
        ssh, p = self._open()
        try:
            rc, out = ssh.run("python3 -V 2>/dev/null || true; " + f"{p['python']} -V", 20)
            rc2, project = ssh.run(f"test -d {p['project_dir']}/backend && echo ok || echo missing", 15)
            return {
                "ok": project.strip().endswith("ok"),
                "host": p["host"],
                "ssh_port": p["ssh_port"],
                "username": p["username"],
                "fingerprint": ssh.fingerprint,
                "python": out.strip().splitlines()[-1] if out.strip() else "",
                "project_dir": p["project_dir"],
                "project_present": project.strip().endswith("ok"),
            }
        finally:
            ssh.close()

    def list(self) -> dict:
        ssh, p = self._open()
        try:
            python = self._python_for(ssh, p)
            rc, out = ssh.run(self._base_cmd(p, "list --json", python), 120)
        finally:
            ssh.close()
        if rc != 0:
            raise AppError("CLOUD_ENGINES_FAILED", f"云端 engines_cli list 失败：{out[:300]}")
        start = out.find("{")
        end = out.rfind("}")
        if start < 0 or end <= start:
            raise AppError("CLOUD_ENGINES_FAILED", f"云端返回无法解析：{out[:300]}")
        try:
            data = json.loads(out[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AppError("CLOUD_ENGINES_FAILED", f"云端返回不是合法 JSON：{exc}") from exc
        data["source"] = "cloud"
        data["host"] = p["host"]
        return data

    # ---------------- 变更 ----------------
    def remove(self, engine_id: str) -> dict:
        ssh, p = self._open()
        try:
            python = self._python_for(ssh, p)
            rc, out = ssh.run(self._base_cmd(p, f"remove {engine_id}", python), 300)
        finally:
            ssh.close()
        if rc != 0:
            raise AppError("CLOUD_ENGINES_FAILED", f"删除失败：{out[:300]}")
        bus.publish("cloud-engines", "engine.removed", {"engine_id": engine_id})
        return {"ok": True, "engine_id": engine_id, "message": out.strip()[:300]}

    def select(self, engine_id: str) -> dict:
        ssh, p = self._open()
        try:
            python = self._python_for(ssh, p)
            rc, out = ssh.run(self._base_cmd(p, f"select {engine_id}", python), 120)
        finally:
            ssh.close()
        if rc != 0:
            raise AppError("CLOUD_ENGINES_FAILED", f"切换失败：{out[:300]}")
        bus.publish("cloud-engines", "engine.selected", {"engine_id": engine_id})
        return {"ok": True, "engine_id": engine_id, "message": out.strip()[:300]}

    # ---------------- 安装（后台 + 流式） ----------------
    def install(self, engine_id: str) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise AppError("CLOUD_ENGINES_BUSY", "云端引擎任务正在运行，等它结束再试")
            job = {
                "job_id": uuid.uuid4().hex[:12],
                "engine_id": engine_id,
                "status": "running",
                "phase": "",
                "progress": 0.0,
                "logs": [],
                "created_at": time.time(),
                "updated_at": time.time(),
                "error": None,
            }
            self._jobs[job["job_id"]] = job
            self._current = job["job_id"]
            self._save(job)
            self._thread = threading.Thread(target=self._run_install, args=(job["job_id"], engine_id), daemon=True)
            self._thread.start()
        return {"job_id": job["job_id"], "engine_id": engine_id}

    def _save(self, job: dict) -> None:
        try:
            path = self._job_dir() / f"{job['job_id']}.json"
            path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _append(self, job: dict, line: str) -> None:
        line = _clean(line.rstrip())
        if not line:
            return
        match = _PROGRESS_RE.match(line)
        if match:
            job["phase"] = match.group("phase").strip()
            job["progress"] = round(int(match.group("pct")) / 100.0, 3)
        job["logs"].append({"ts": time.time(), "message": line[:400]})
        job["logs"] = job["logs"][-200:]
        job["updated_at"] = time.time()

    def _run_install(self, job_id: str, engine_id: str) -> None:
        job = self._jobs[job_id]
        ssh = None
        try:
            ssh, p = self._open()
            job["host"] = p["host"]
            self._append(job, f"在 {p['host']} 上安装 {engine_id}（hf-mirror）")
            python = self._python_for(ssh, p)
            cmd = self._base_cmd(p, f"install {engine_id}", python)
            stdin, stdout, stderr = ssh._client.exec_command(cmd, timeout=7200)  # noqa: SLF001
            for raw in iter(stdout.readline, ""):
                self._append(job, raw)
                if len(job["logs"]) % 10 == 0:
                    self._save(job)
                    bus.publish("cloud-engines", "job.update", {"job_id": job_id, **{
                        "status": job["status"], "phase": job["phase"], "progress": job["progress"]}})
            rc = stdout.channel.recv_exit_status()
            tail = stderr.read().decode("utf-8", "replace").strip()
            if tail:
                self._append(job, "[stderr] " + tail[-500:])
            if rc == 0:
                job["status"] = "completed"
                job["progress"] = 1.0
                self._append(job, "安装完成")
            else:
                job["status"] = "failed"
                job["error"] = (tail or "安装失败")[-500:]
                self._append(job, f"安装失败（exit {rc}）")
        except AppError as exc:
            job["status"] = "failed"
            job["error"] = f"{exc.code}: {exc.message}"
            self._append(job, f"失败：{exc.code} {exc.message} {exc.detail}".strip())
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            self._append(job, f"失败：{type(exc).__name__}: {exc}")
        finally:
            if ssh is not None:
                ssh.close()
            job["updated_at"] = time.time()
            self._save(job)
            bus.publish("cloud-engines", "job.final", {"job_id": job_id, "status": job["status"],
                                                       "engine_id": engine_id, "error": job.get("error")})

    # ---------------- 查询任务 ----------------
    def job(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            path = self._job_dir() / f"{job_id}.json"
            if not path.exists():
                raise AppError("NOT_FOUND", "云端引擎任务不存在")
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AppError("NOT_FOUND", "云端引擎任务记录已损坏") from exc
        return job

    def _mark_stale(self, job: dict) -> dict:
        """文件里留下的 running 但实际上没线程在跑 → 标成中断。"""
        if job.get("status") == "running" and not (self._thread is not None and self._thread.is_alive()):
            job = dict(job)
            job["status"] = "interrupted"
            job["error"] = job.get("error") or "服务重启导致安装中断，可重新点安装（已下载的部分会续传）"
        return job

    def jobs(self, limit: int = 10) -> dict:
        items = [self._mark_stale(j) for j in list(self._jobs.values())]
        for path in sorted(self._job_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if len(items) >= limit:
                break
            if any(j["job_id"] == path.stem for j in items):
                continue
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        items = [self._mark_stale(j) for j in items]
        items.sort(key=lambda j: j.get("created_at", 0), reverse=True)
        return {"items": items[:limit]}
