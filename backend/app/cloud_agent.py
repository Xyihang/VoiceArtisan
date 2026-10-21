"""云主机一键接入 Agent：从「只有连接信息」到「本地可用云端推理」。

设计（见 docs/spec/CLOUD-AGENT-1.md）：
- 8 步状态机是保底骨架，不依赖任何外部服务也能跑完；
- LLM（OpenAI 兼容，默认 DeepSeek `deepseek-chat`）只做「失败诊断 / 下一步建议」，
  不可用时自动降级，不影响任务完成；
- SSH 密码只存在内存，状态文件与日志全部脱敏；本地配置通过既有 update_runtime 落盘。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import tarfile
import threading
import time
import uuid
from pathlib import Path

import httpx
from typing import Any

from .errors import AppError
from .events import bus

logger = logging.getLogger(__name__)

TOPIC = "cloud-agent"
_JOB_DIR = Path("data") / "cloud-agent" / "jobs"
_SECRET_RE = re.compile(
    r"(?i)(password|passwd|passphrase|api[_-]?key|x-api-key|secret)\s*[:=]\s*\S+"
)
_STEPS = ("S0", "S1", "S2", "S3", "S4", "S4B", "S5", "S6", "S7", "S8")
STEP_TITLES = {
    "S0": "参数校验",
    "S1": "SSH 连通",
    "S2": "环境体检",
    "S3": "上传源码",
    "S4": "安装依赖",
    "S4B": "下载模型",
    "S5": "启动服务",
    "S6": "就绪探测",
    "S7": "写入本地配置",
    "S8": "本地验收",
}
NEXT_ACTIONS = {
    "SSH_UNREACHABLE": ["确认云主机已开机与 IP 正确", "确认安全组放行 SSH 端口", "换网络后点「重试本步」"],
    "SSH_AUTH_FAILED": ["确认用户名/密码", "或改用私钥路径", "确认该用户被 sshd 允许登录"],
    "SSH_HOSTKEY_CHANGED": ["实例重装/迁移后属正常", "勾选「重置主机指纹」后重试", "或手动删除 data/cloud-agent/known_hosts"],
    "REMOTE_PYTHON_TOO_OLD": ["在云端安装 Python 3.10+", "或改用镜像自带 3.10 的系统"],
    "REMOTE_NO_GPU": ["确认是 GPU 实例且已装驱动", "在云端执行 nvidia-smi 自查"],
    "REMOTE_DISK_LOW": ["清理云端磁盘或扩容到 ≥ 30GB"],
    "REMOTE_PORT_BUSY": ["换一个远端端口（如 18444）后重试", "或在云端释放该端口"],
    "UPLOAD_FAILED": ["检查磁盘余量与权限", "重试本步"],
    "REMOTE_DEPS_FAILED": ["检查云端网络/镜像源", "查看本步日志里的 pip 报错"],
    "REMOTE_CUDA_UNAVAILABLE": ["确认实例是有卡模式（非无卡/CPU 机型）", "确认 nvidia-smi 正常", "换镜像源重试本步"],
    "REMOTE_MODEL_DOWNLOAD_FAILED": ["查看云端 logs/download.log", "确认磁盘余量 ≥ 15GB", "重试本步（支持断点续传）"],
    "REMOTE_START_FAILED": ["查看云端 logs/app.log", "确认端口未被占用"],
    "CLOUD_NOT_READY": ["模型未就绪时可在云端下载模型", "确认云端 8443 可从本地访问或改用隧道"],
    "CONFIG_WRITE_FAILED": ["检查本地 config 目录权限"],
    "VERIFY_FAILED": ["点「试听一次」自查", "查看本地 logs/app.log"],
}


def redact(text: str) -> str:
    """日志/状态落盘前的统一脱敏。"""
    if not text:
        return ""
    return _SECRET_RE.sub(lambda m: m.group(1) + ": ***", text)


def _protect(plain: str) -> str:
    if not plain:
        return ""
    try:
        from .secretstore import protect

        return protect(plain)
    except Exception:  # noqa: BLE001
        return ""


def _unprotect(blob: str) -> str:
    if not blob:
        return ""
    try:
        from .secretstore import unprotect

        return unprotect(blob)
    except Exception:  # noqa: BLE001
        return ""


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _now() -> float:
    return time.time()


class JobStore:
    """任务状态落盘：data/cloud-agent/jobs/<job_id>.json，只保留最近 10 个。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.dir = Path(data_dir or _JOB_DIR)
        self._lock = threading.RLock()

    def path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def save(self, job: dict) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            job["updated_at"] = _now()
            payload = dict(job)
            payload.pop("_secret", None)
            self.path(job["job_id"]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._prune()

    def load(self, job_id: str) -> dict | None:
        p = self.path(job_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list(self) -> list[dict]:
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def delete(self, job_id: str) -> None:
        p = self.path(job_id)
        if p.exists():
            p.unlink()

    def _prune(self) -> None:
        files = sorted(self.dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in files[10:]:
            try:
                old.unlink()
            except OSError:
                pass


class LLMClient:
    """OpenAI 兼容客户端：只用于失败诊断，绝不接收凭据。"""

    def __init__(self, cfg: Any) -> None:
        llm = getattr(getattr(cfg, "agent", None), "llm", None)
        self.enabled = bool(getattr(llm, "enabled", False))
        self.base_url = (os.environ.get("QWENTTS_AGENT_BASE_URL") or getattr(llm, "base_url", "") or "").rstrip("/")
        self.model = os.environ.get("QWENTTS_AGENT_MODEL") or getattr(llm, "model", "deepseek-chat")
        self.api_key = os.environ.get("QWENTTS_AGENT_KEY") or getattr(llm, "api_key", "")
        self.timeout_s = float(getattr(llm, "timeout_s", 60) or 60)
        self.max_turns = int(getattr(llm, "max_turns", 12) or 12)
        self.reason: str = ""

    def available(self) -> bool:
        if not (self.enabled and self.api_key and self.base_url):
            self.reason = "未配置 agent.llm（enabled/api_key/base_url）"
            return False
        return True

    def list_models(self) -> list[str]:
        if not self.available():
            return []
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(
                    self.base_url + "/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                r.raise_for_status()
                return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        except Exception as exc:  # noqa: BLE001
            self.reason = f"模型列表不可用：{exc}"
            return []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict | None:
        """一轮对话；失败返回 None（调用方据此回退）。"""
        if not self.available():
            return None
        body: dict = {"model": self.model, "temperature": 0, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                r = client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.api_key},
                    json=body,
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:  # noqa: BLE001
            self.reason = "对话请求失败：" + str(exc)
            logger.warning("cloud-agent LLM 对话失败：%s", exc)
            return None

    def diagnose(self, step: str, code: str, message: str, env: dict) -> list[str]:
        """返回「下一步建议」。失败一律返回空列表（由本地规则补位）。"""
        if not self.available():
            return []
        models = []
        try:
            models = self.list_models()
            if models and self.model not in models:
                self.reason = f"模型不可用：{self.model}；可用：{', '.join(models[:5])}"
                logger.warning("cloud-agent LLM 降级：%s", self.reason)
                return []
        except Exception:  # noqa: BLE001
            pass
        prompt = (
            "你在诊断一个语音合成系统的云主机接入失败。只输出 2-3 条中文动作建议，"
            "每条以动词开头，不要解释、不要编号以外的内容。\n"
            f"步骤：{step} ({STEP_TITLES.get(step, '')})\n"
            f"错误码：{code}\n错误信息：{message}\n"
            f"环境摘要：{json.dumps(env, ensure_ascii=False)[:1200]}"
        )
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                r = client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "temperature": 0,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
            lines = [ln.strip(" -·\t") for ln in text.splitlines() if ln.strip()]
            return [redact(ln) for ln in lines][:3]
        except Exception as exc:  # noqa: BLE001
            self.reason = f"诊断请求失败：{exc}"
            logger.warning("cloud-agent LLM 诊断失败，降级：%s", exc)
            return []



# ---------------------------------------------------------------- AI 自主排障
AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remote_exec",
            "description": "在云主机执行预注册命令（只能选下面的 cmd_id，不能自定义 shell）",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd_id": {
                        "type": "string",
                        "enum": [
                            "env_probe", "check_port", "tail_log", "disk_free",
                            "pip_sync", "start_service", "stop_service", "restart_service",
                            "read_remote_file", "write_remote_config",
                        ],
                    },
                    "args": {"type": "object"},
                },
                "required": ["cmd_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_step",
            "description": "重跑确定性流程里的某一步（修好环境后用）",
            "parameters": {
                "type": "object",
                "properties": {"step_id": {"type": "string", "enum": ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]}},
                "required": ["step_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "give_up",
            "description": "无法自动修复时调用，写清原因",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]

AI_SYSTEM_PROMPT = (
    "你是语音合成系统云主机接入的排障代理。只允许调用给定工具，不要输出其它内容。"
    "先判断错误属于认证、网络、端口、依赖还是磁盘问题，再用 remote_exec 收集证据，"
    "确认能修就执行修复命令（pip_sync / start_service），然后用 run_step 重跑失败步骤；"
    "确实修不了就调用 give_up 并写明原因。每次只做一件事。"
)

class _SSH:
    """paramiko 薄封装：只做「连上、执行、传文件」，密码不出本进程。"""

    def __init__(self, host: str, port: int, user: str, password: str = "", key_path: str = "", passphrase: str = "") -> None:
        self.host, self.port, self.user = host, int(port), user
        self._password, self._key_path, self._passphrase = password, key_path, passphrase
        self._client = None
        self.fingerprint = ""

    def connect(self, timeout: float = 15.0) -> None:
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {"username": self.user, "timeout": timeout, "banner_timeout": timeout,
                        "auth_timeout": timeout, "allow_agent": False, "look_for_keys": False}
        if self._key_path:
            kwargs["key_filename"] = self._key_path
            if self._passphrase:
                kwargs["passphrase"] = self._passphrase
        else:
            kwargs["password"] = self._password
        try:
            client.connect(self.host, self.port, **kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            code = "SSH_AUTH_FAILED" if ("auth" in msg or "password" in msg or "permission" in msg) else "SSH_UNREACHABLE"
            raise AppError(code, redact(str(exc))) from exc
        key = client.get_transport().get_remote_server_key()
        self.fingerprint = f"{key.get_name()} {key.get_fingerprint().hex()}"
        self._client = client

    def run(self, cmd: str, timeout: float = 30.0) -> tuple[int, str]:
        assert self._client is not None
        stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, redact((out + err).strip())

    def put(self, local: Path, remote: str) -> None:
        assert self._client is not None
        sftp = self._client.open_sftp()
        try:
            sftp.put(str(local), remote)
        finally:
            sftp.close()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


class CloudAgent:
    """单例：同一时刻只允许一个接入任务。"""

    def __init__(self, cfg_manager, data_dir: Path | None = None, on_applied=None) -> None:
        self.cfg_manager = cfg_manager
        self.on_applied = on_applied
        self.store = JobStore(Path(data_dir or cfg_manager.app.data_dir) / "cloud-agent" / "jobs")
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._current: str = ""
        self._cancel = threading.Event()
        self._secrets: dict[str, dict] = {}
        self._cred: dict[str, dict] = {}
        self._tunnels: dict[str, object] = {}
        self._last_log_save: dict[str, float] = {}
        self._active_ssh: "_SSH | None" = None
        self._active_llm = None
        self._mark_interrupted()

    def _mark_interrupted(self) -> None:
        """服务重启后，把上次残留的 running 任务标成 failed，避免界面一直“进行中”。"""
        try:
            for job in self.store.list():
                if job.get("status") != "running":
                    continue
                job["status"] = "failed"
                job["error"] = {
                    "code": "INTERRUPTED",
                    "message": "后端重启导致任务中断，可点「续做」从失败步骤继续",
                    "next_actions": ["点「续做（跳过已完成步骤）」", "或重新发起接入"],
                    "step": job.get("current_step", ""),
                }
                for step in job.get("steps", []):
                    if step.get("state") == "running":
                        step["state"] = "failed"
                self.store.save(job)
                logger.info("cloud-agent 任务 %s 标记为中断", job.get("job_id"))
        except Exception:  # noqa: BLE001
            logger.debug("标记中断任务失败", exc_info=True)

    # ---------------- 对外 ----------------
    def busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @staticmethod
    def _normalize_payload(payload: dict) -> dict:
        """允许用户直接把 ssh 登录指令贴进 host 字段。"""
        from .sshcmd import looks_like_ssh_command, parse_ssh_command

        data = dict(payload or {})
        host = str(data.get("host") or "")
        if looks_like_ssh_command(host):
            parsed = parse_ssh_command(host)
            if parsed["host"]:
                data["host"] = parsed["host"]
            if parsed["port"] and not data.get("ssh_port"):
                data["ssh_port"] = parsed["port"]
            if parsed["username"] and not data.get("username"):
                data["username"] = parsed["username"]
            if parsed["key_path"]:
                auth = dict(data.get("auth") or {})
                auth.setdefault("type", "key")
                auth["key_path"] = parsed["key_path"]
                data["auth"] = auth
        return data

    def start(self, payload: dict, resume_of: str | None = None) -> dict:
        payload = self._normalize_payload(payload)
        with self._lock:
            if self.busy():
                raise AppError("BUSY", "已有接入任务在执行，请等待完成或先取消。")
            if resume_of:
                job = self.store.load(resume_of)
                if job is None:
                    raise AppError("INVALID_INPUT", "任务不存在或已过期")
            else:
                job = self._new_job(payload)
            job["resume"] = bool(resume_of)
            auth = payload.get("auth") or {}
            self._cred[job["job_id"]] = {
                "password": auth.get("password", ""),
                "key_path": auth.get("key_path", ""),
                "passphrase": auth.get("passphrase", ""),
            }
            self._secrets[job["job_id"]] = dict(self._secrets.get(resume_of or "", {}))
            self._current = job["job_id"]
            self._cancel.clear()
            job["status"] = "running"
            job["error"] = {}
            self.store.save(job)
            self._thread = threading.Thread(target=self._run, args=(job["job_id"], payload), daemon=True)
            self._thread.start()
            return {"job_id": job["job_id"], "status": "running"}

    def cancel(self, job_id: str) -> dict:
        if job_id != self._current:
            return {"job_id": job_id, "status": "idle"}
        self._cancel.set()
        return {"job_id": job_id, "status": "cancelling"}

    def get(self, job_id: str) -> dict:
        job = self.store.load(job_id)
        if job is None:
            raise AppError("INVALID_INPUT", "任务不存在")
        job["ai_available"] = LLMClient(self.cfg_manager.app).available()
        return job

    def jobs(self) -> list[dict]:
        out = []
        for job in self.store.list():
            out.append({k: job.get(k) for k in ("job_id", "status", "current_step", "created_at", "updated_at", "target", "result", "error")})
        return out

    # ---------------- 内部 ----------------
    def _new_job(self, payload: dict) -> dict:
        job_id = uuid.uuid4().hex[:12]
        return {
            "job_id": job_id,
            "created_at": _now(),
            "updated_at": _now(),
            "status": "queued",
            "current_step": "S0",
            "target": {
                "host": payload.get("host", ""),
                "ssh_port": int(payload.get("ssh_port") or 22),
                "username": payload.get("username", ""),
                "auth_type": (payload.get("auth") or {}).get("type", "password"),
            },
            "options": payload.get("options") or {},
            "steps": [{"id": s, "title": STEP_TITLES[s], "state": "pending"} for s in _STEPS],
            "env": {},
            "remote": {},
            "result": {},
            "error": {},
            "logs": [],
        }

    def _emit(self, job: dict, event: str, data: dict) -> None:
        payload = {"job_id": job["job_id"], **data}
        bus.publish(TOPIC, event, payload)

    def _log(self, job: dict, line: str) -> None:
        line = redact(line)[:500]
        job.setdefault("logs", []).append(line)
        job["logs"] = job["logs"][-200:]
        self._emit(job, "log", {"step": job["current_step"], "line": line})
        logger.info("cloud-agent[%s] %s", job["job_id"], line)
        last = self._last_log_save.get(job["job_id"], 0.0)
        if _now() - last > 3.0:
            self._last_log_save[job["job_id"]] = _now()
            self.store.save(job)

    @staticmethod
    def _state_of(job: dict, step_id: str) -> str:
        for step in job.get("steps", []):
            if step["id"] == step_id:
                return step["state"]
        return "pending"

    def _note(self, job: dict, step_id: str, text: str) -> None:
        """把实时进度写进步骤 detail（界面能直接看到，不用等失败）。"""
        text = redact(text)[:180]
        for step in job.get("steps", []):
            if step["id"] == step_id:
                step["detail"] = text
        self.store.save(job)
        self._emit(job, "step", {"step": step_id, "state": "running", "detail": text})

    def _mark_skipped(self, job: dict, step_id: str) -> None:
        for step in job["steps"]:
            if step["id"] == step_id:
                step["state"] = "skipped"
                step["detail"] = "已完成，跳过"
                step["ended_at"] = _now()
        self.store.save(job)
        self._emit(job, "step", {"step": step_id, "state": "skipped", "detail": "已完成，跳过"})

    def _step(self, job: dict, step_id: str) -> None:
        job["current_step"] = step_id
        for s in job["steps"]:
            if s["id"] == step_id:
                s["state"] = "running"
                s["started_at"] = _now()
        self.store.save(job)
        self._emit(job, "step", {"step": step_id, "state": "running", "title": STEP_TITLES[step_id]})

    def _ok(self, job: dict, step_id: str, detail: str = "") -> None:
        for s in job["steps"]:
            if s["id"] == step_id:
                s["state"] = "ok"
                s["detail"] = redact(detail)[:300]
                s["ended_at"] = _now()
        self.store.save(job)
        self._emit(job, "step", {"step": step_id, "state": "ok", "detail": redact(detail)[:300]})

    def _fail(self, job: dict, step_id: str, code: str, message: str, llm: LLMClient) -> None:
        for s in job["steps"]:
            if s["id"] == step_id:
                s["state"] = "failed"
                s["detail"] = redact(message)[:300]
        actions = list(NEXT_ACTIONS.get(code, ["查看日志后点「重试本步」"]))
        extra: list[str] = []
        if (job.get("options") or {}).get("ai_mode") and llm.available():
            try:
                extra = llm.diagnose(step_id, code, message, job.get("env") or {})
            except Exception:  # noqa: BLE001
                extra = []
        if extra:
            actions = extra + actions[:1]
        job["status"] = "failed"
        job["error"] = {"code": code, "message": redact(message)[:400], "next_actions": actions,
                        "step": step_id, "ai": bool(extra)}
        self.store.save(job)
        self._emit(job, "finished", {"status": "failed", "step": step_id, "error": job["error"]})

    def _run(self, job_id: str, payload: dict) -> None:
        job = self.store.load(job_id) or self._new_job(payload)
        llm = LLMClient(self.cfg_manager.app)
        ssh = _SSH(
            job["target"]["host"], job["target"]["ssh_port"], job["target"]["username"],
            password=(payload.get("auth") or {}).get("password", ""),
            key_path=(payload.get("auth") or {}).get("key_path", ""),
            passphrase=(payload.get("auth") or {}).get("passphrase", ""),
        )
        self._active_ssh = ssh
        self._active_llm = llm
        try:
            self._steps(job, payload, ssh, llm)
        except AppError as exc:
            if exc.code == "CANCELLED":
                job["status"] = "cancelled"
                job["error"] = {"code": "CANCELLED", "message": "任务已取消", "next_actions": ["重新发起接入即可"], "step": job["current_step"]}
                for step in job["steps"]:
                    if step["id"] == job["current_step"]:
                        step["state"] = "cancelled"
                self.store.save(job)
                self._emit(job, "finished", {"status": "cancelled", "step": job["current_step"]})
            else:
                self._fail(job, job["current_step"], exc.code, exc.detail or exc.message, llm)
        except Exception as exc:  # noqa: BLE001
            logger.exception("cloud-agent 未预期异常")
            self._fail(job, job["current_step"], "INTERNAL", f"{type(exc).__name__}: {exc}", llm)
        finally:
            self._active_ssh = None
            self._active_llm = None
            ssh.close()
            self._secrets.pop(job_id, None)
            self._cred.pop(job_id, None)
            tunnel = self._tunnels.pop(job_id, None)
            if tunnel is not None:
                try:
                    tunnel.stop()
                except Exception:  # noqa: BLE001
                    pass
            if job.get("status") == "running":
                job["status"] = "succeeded"
                self.store.save(job)

    def _steps(self, job: dict, payload: dict, ssh: _SSH, llm: LLMClient) -> None:
        handlers = (
            ("S0", self._s0_validate),
            ("S1", self._s1_ssh),
            ("S2", self._s2_env),
            ("S3", self._s3_upload),
            ("S4", self._s4_deps),
            ("S4B", self._s4b_models),
            ("S5", self._s5_start),
            ("S6", self._s6_ready),
            ("S7", self._s7_local_config),
            ("S8", self._s8_verify),
        )
        ctx: dict = {}
        for step_id, handler in handlers:
            if self._cancel.is_set():
                raise AppError("CANCELLED", "任务已取消")
            if job.get("resume") and self._state_of(job, step_id) == "ok":
                self._mark_skipped(job, step_id)
                continue
            self._step(job, step_id)
            try:
                handler(job, payload, ssh, ctx)
            except AppError as exc:
                if exc.code in ("CANCELLED", "INTERRUPTED") or not self._ai_enabled(job, llm):
                    raise
                self._log(job, "AI 排障介入（" + step_id + " / " + exc.code + "）")
                if not self._ai_troubleshoot(job, ssh, llm, step_id, exc.code, exc.detail or exc.message):
                    raise
                self._step(job, step_id)
                handler(job, payload, ssh, ctx)
            if step_id == "S4B" and (job.get("options") or {}).get("prepare_only"):
                for rest in job["steps"]:
                    if rest["state"] in ("pending", "running"):
                        rest["state"] = "skipped"
                        rest["detail"] = "无卡模式：暂不启动服务（开机后点「续做」继续）"
                job["status"] = "succeeded"
                job["result"]["prepare_only"] = True
                self.store.save(job)
                self._remember_host(job, payload)
                self._emit(job, "finished", {"status": "succeeded", "step": "S4B", "result": job["result"]})
                return
        job["status"] = "succeeded"
        self.store.save(job)
        self._remember_host(job, payload)
        self._emit(job, "finished", {"status": "succeeded", "step": "S8", "result": job["result"]})

    def _remember_host(self, job: dict, payload: dict) -> None:
        """接入成功后把 SSH 档案存下来，供「云端引擎管理」复用（密码走 DPAPI）。"""
        try:
            from . import cloud_hosts

            target = job.get("target") or {}
            cred = self._cred.get(job["job_id"]) or {}
            options = job.get("options") or {}
            project_dir = str(options.get("remote_dir") or "~/qwentts").rstrip("/")
            env = job.get("env") or {}
            cloud_hosts.save(
                Path(self.cfg_manager.app.data_dir),
                {
                    "host": target.get("host", ""),
                    "ssh_port": int(target.get("ssh_port") or 22),
                    "username": target.get("username", ""),
                    "password": cred.get("password") or (payload.get("auth") or {}).get("password", ""),
                    "project_dir": project_dir or cloud_hosts.DEFAULT_PROJECT_DIR,
                    "python": str(env.get("python"))
                    if cloud_hosts.looks_like_python_path(env.get("python"))
                    else cloud_hosts.DEFAULT_PYTHON,
                },
            )
            self._log(job, "已保存云端主机档案：云端引擎管理可直接使用（" + project_dir + "）")
        except Exception as exc:  # noqa: BLE001 - 档案保存失败不影响接入结果
            logger.warning("保存云端主机档案失败：%s", exc)

    # ---- 单步实现 ----
    def _s0_validate(self, job, payload, ssh, ctx) -> None:
        if not re.match(r"^[A-Za-z0-9_.:\-]{3,253}$", job["target"]["host"]):
            raise AppError("INVALID_INPUT", "主机地址格式不正确")
        if not job["target"]["username"]:
            raise AppError("INVALID_INPUT", "用户名不能为空")
        auth = payload.get("auth") or {}
        if auth.get("type") == "key":
            if not Path(auth.get("key_path", "")).exists():
                raise AppError("INVALID_INPUT", "私钥文件不存在：" + str(auth.get("key_path")))
        elif not auth.get("password"):
            raise AppError("INVALID_INPUT", "密码不能为空（或改用私钥）")
        self._ok(job, "S0", "参数通过")

    def _s1_ssh(self, job, payload, ssh, ctx) -> None:
        ssh.connect()
        ctx["ssh"] = ssh
        known = self.store.dir.parent / "known_hosts"
        known.parent.mkdir(parents=True, exist_ok=True)
        prev = known.read_text(encoding="utf-8").splitlines() if known.exists() else []
        entry = job["target"]["host"] + " " + ssh.fingerprint
        if entry not in prev:
            host = job["target"]["host"]
            changed = any(line.startswith(host + " ") for line in prev)
            if changed and not (job.get("options") or {}).get("trust_hostkey"):
                raise AppError(
                    "SSH_HOSTKEY_CHANGED",
                    "主机指纹与上次不同（实例重装/迁移后属正常）。确认无误后勾选「重置主机指纹」再试",
                )
            if changed:
                prev = [line for line in prev if not line.startswith(host + " ")]
                self._log(job, "主机指纹已变更，按用户确认覆盖（新：" + ssh.fingerprint + "）")
            known.write_text("\n".join(prev + [entry]) + "\n", encoding="utf-8")
        self._ok(job, "S1", ssh.fingerprint)

    def _s2_env(self, job, payload, ssh, ctx) -> None:
        opts = job.get("options") or {}
        remote_port = int(opts.get("remote_port") or 8443)
        probe = (
            "PB=$(command -v python3 || command -v python || true); "
            "[ -z \"$PB\" ] && for cand in /root/miniconda3/bin/python3 /root/miniconda3/bin/python /opt/conda/bin/python3 /opt/conda/bin/python /usr/bin/python3; do [ -x \"$cand\" ] && PB=$cand && break; done; "
            "echo PY=$PB; echo PYVER=$($PB -V 2>&1 | head -1); "
            "echo GPU=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1); "
            "echo DISK=$(df -m ~ | tail -1 | awk '{print $4}'); "
            "echo PORTBUSY=$((command -v ss >/dev/null && ss -ltn || netstat -ltn) 2>/dev/null | grep -c ':" + str(remote_port) + " ' || true); "
            "echo FFMPEG=$(command -v ffmpeg >/dev/null && echo yes || echo no); "
            "echo OS=$(. /etc/os-release 2>/dev/null; echo $PRETTY_NAME); "
            "echo DATA_DIR=$([ -d /root/autodl-tmp ] && echo /root/autodl-tmp || echo none); "
            "echo DATA_DISK=$(df -m /root/autodl-tmp 2>/dev/null | tail -1 | awk '{print $4}' || echo 0)"
        )
        rc, out = ssh.run(probe, timeout=40)
        if rc != 0:
            raise AppError("SSH_UNREACHABLE", "体检命令执行失败：" + out[:200])
        env = self._parse_env(out, remote_port)
        job["env"] = env
        self._log(job, "体检：" + json.dumps(env, ensure_ascii=False))
        py = str(env.get("python", ""))
        m = re.search(r"(\d+)\.(\d+)", py)
        major, minor = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        if not ((major, minor) >= (3, 9) and (major, minor) <= (3, 13)):
            raise AppError("REMOTE_PYTHON_TOO_OLD", "云端 Python 版本不支持（" + (py or "未知") + "），需要 3.9-3.13（推荐 3.10）")
        if not env.get("gpu"):
            if (job.get("options") or {}).get("allow_no_gpu") or (job.get("options") or {}).get("prepare_only"):
                env["gpu"] = "无卡模式（未检测到 GPU，仅准备环境）"
                self._log(job, "未检测到 GPU：按「无卡模式」继续准备环境")
            else:
                raise AppError("REMOTE_NO_GPU", "未检测到 GPU（nvidia-smi 无输出）；无卡模式请勾选「仅准备环境」")
        free_mb = int(env.get("data_free_mb") or env.get("disk_free_mb") or 0)
        if free_mb < 20000:
            raise AppError("REMOTE_DISK_LOW", "云端磁盘余量不足（" + str(env.get("disk_free_mb")) + "MB，建议 ≥20GB）")
        if env.get("port_busy"):
            raise AppError("REMOTE_PORT_BUSY", "远端端口 " + str(remote_port) + " 已被占用，请换端口")
        self._ok(job, "S2", str(env.get("python")) + " / " + str(env.get("gpu")))

    def _s3_upload(self, job, payload, ssh, ctx) -> None:
        remote_dir = ((job.get("options") or {}).get("remote_dir") or "~/qwentts").rstrip("/")
        tarball = self._build_tarball()
        digest = _sha256(tarball)
        # SFTP 不认 ~，先解析成绝对路径
        rc, abs_dir = ssh.run("mkdir -p " + remote_dir + " && cd " + remote_dir + " && pwd", timeout=60)
        lines = [l for l in abs_dir.strip().splitlines() if l.startswith("/")]
        if not lines:
            raise AppError("UPLOAD_FAILED", "无法解析远端目录：" + abs_dir[:200])
        remote_dir = lines[-1]
        job["remote"]["path"] = remote_dir
        rc, out = ssh.run("mkdir -p " + remote_dir + " && cat " + remote_dir + "/.src.sha 2>/dev/null || true", timeout=30)
        if digest in out:
            self._ok(job, "S3", "源码未变，跳过上传")
            job["remote"]["path"] = remote_dir
            try:
                tarball.unlink()
            except OSError:
                pass
            return
        try:
            ssh.put(tarball, remote_dir + "/qwentts-src.tar.gz")
            rc, out = ssh.run("cd " + remote_dir + " && tar xzf qwentts-src.tar.gz && echo " + digest + " > .src.sha && ls -d backend", timeout=300)
            if rc != 0:
                raise AppError("UPLOAD_FAILED", "解包失败：" + out[:200])
        finally:
            try:
                tarball.unlink()
            except OSError:
                pass
        job["remote"]["path"] = remote_dir
        self._ok(job, "S3", remote_dir)

    def _s4_deps(self, job, payload, ssh, ctx) -> None:
        opts = job.get("options") or {}
        remote_dir = job["remote"].get("path") or "~/qwentts"
        index = opts.get("pip_index") or "https://pypi.tuna.tsinghua.edu.cn/simple"
        # 无卡模式（仅准备环境）允许 torch 没有 CUDA，否则 S2 放行了 S4 又会卡死
        no_gpu = bool(opts.get("allow_no_gpu") or opts.get("prepare_only"))
        cuda_assert = "" if no_gpu else "; assert torch.cuda.is_available()"
        # 依赖自检默认必须包含 CUDA：只有 CPU 版 torch 时不能算“已就绪”
        rc, out = ssh.run(
            "cd " + remote_dir + " && [ -x .venv/bin/python ] && .venv/bin/python -c "
            "\"import fastapi,uvicorn,soundfile,torch" + cuda_assert + "\" 2>/dev/null "
            "&& echo deps_ok || echo deps_missing",
            timeout=300,
        )
        if "deps_ok" in out:
            self._ok(job, "S4", "依赖已就绪，跳过安装")
            return
        py = (job.get("env") or {}).get("python_bin") or "python3"
        self._log(job, "开始安装依赖，首次约 5-15 分钟…")
        # 两段式，且**先装 torch**：qwen-tts 等包会声明 torch 依赖，
        # 若先装普通依赖，pip 会从国内镜像拉一份 CPU 版 torch（几百 MB 白装），
        # 所以先按 pytorch 源把 cu121 版装好，后面普通依赖看到 torch 已满足就不会再动它。
        rc, out = ssh.run(
            "cd " + remote_dir + " && " + py + " -m venv .venv && "
            ".venv/bin/pip install -q -U pip -i " + index,
            timeout=600,
        )

        torch_ok = False
        # 国内优先走上交镜像（官方源经常几十 KB/s 甚至超时），官方源只作兜底
        for idx in (
            "https://mirror.sjtu.edu.cn/pytorch-wheels/cu121",
            "https://download.pytorch.org/whl/cu121",
        ):
            self._log(job, "① 安装 GPU 版 torch（" + idx.split("/")[2] + "，约 2.5GB；国内优先镜像）…")
            rc, tout = ssh.run(
                "cd " + remote_dir + " && .venv/bin/pip install -q --no-cache-dir "
                "-r requirements-torch.txt --index-url " + idx + " && echo torch_ok",
                timeout=2400,
            )
            if "torch_ok" in tout:
                torch_ok = True
                break
            self._log(job, "该源安装失败：" + tout[-200:])
        if not torch_ok:
            raise AppError(
                "REMOTE_DEPS_FAILED",
                "GPU 版 torch 安装失败（pytorch 官方源与上交镜像都不通）。"
                "请手动执行：.venv/bin/pip install -r requirements-torch.txt "
                "--index-url https://download.pytorch.org/whl/cu121",
            )

        self._log(job, "② 安装其余依赖（" + index + "）…")
        rc, out = ssh.run(
            "cd " + remote_dir + " && .venv/bin/pip install -q -r requirements.txt -i " + index + " && echo base_ok",
            timeout=1800,
        )
        if "base_ok" not in out:
            raise AppError("REMOTE_DEPS_FAILED", "普通依赖安装失败：" + out[-400:])

        # 关键：pip 默认装的是 CPU 版 torch，云端必须换成 CUDA 版，否则模型加载必失败
        rc, cuda_out = ssh.run(
            "cd " + remote_dir + " && .venv/bin/python -c "
            "\"import torch;print('cuda', torch.cuda.is_available(), torch.__version__)\" 2>&1 | tail -1",
            timeout=300,
        )
        self._log(job, "CUDA 自检：" + cuda_out.strip()[:120])
        if "cuda True" not in cuda_out and no_gpu:
            self._log(job, "无卡模式：torch 已是 cu121 版，当前看不到 GPU 属预期；开卡后直接可用")
            self._ok(job, "S4", "依赖就绪（cu121 torch 已装，无卡模式）")
            return
        if "cuda True" not in cuda_out:
            self._log(job, "torch 未启用 CUDA，正在安装 cu121 版本（约 2.5GB，3-8 分钟）…")
            for idx in (
                "https://mirror.sjtu.edu.cn/pytorch-wheels/cu121",
                "https://download.pytorch.org/whl/cu121",
            ):
                rc, fix_out = ssh.run(
                    "cd " + remote_dir + " && .venv/bin/pip install -q --no-cache-dir "
                    "torch==2.4.1 torchaudio==2.4.1 --index-url " + idx,
                    timeout=1800,
                )
                rc, cuda_out = ssh.run(
                    "cd " + remote_dir + " && .venv/bin/python -c "
                    "\"import torch;print('cuda', torch.cuda.is_available(), torch.__version__)\" 2>&1 | tail -1",
                    timeout=300,
                )
                self._log(job, "CUDA 自检（" + idx.split("/")[2] + "）：" + cuda_out.strip()[:120])
                if "cuda True" in cuda_out:
                    break
            if "cuda True" not in cuda_out:
                raise AppError(
                    "REMOTE_CUDA_UNAVAILABLE",
                    "云端 torch 看不到 CUDA（" + cuda_out.strip()[:160] + "）。"
                    "常见原因：实例是无卡模式、驱动未加载，或 pip 源没有 cu121 轮子",
                )
        self._ok(job, "S4", "venv 与依赖就绪（CUDA 可用）")

    def _s4b_models(self, job, payload, ssh, ctx) -> None:
        """在云端下载 TTS 模型（ModelScope 国内直连，断点续传）。默认由程序完成，用户无需上传。"""
        opts = job.get("options") or {}
        remote_dir = job["remote"].get("path") or "~/qwentts"
        size = str(opts.get("model_size") or "1.7B")
        env = job.get("env") or {}
        data_root = env.get("data_dir") or ""
        models_dir = (data_root + "/qwentts-models") if data_root else (remote_dir + "/models")
        # 即使跳过下载也要记下 models_dir：S5 写云端配置时要指向数据盘，复用已有模型
        job["remote"]["models_dir"] = models_dir
        if opts.get("download_models") is False:
            rc, exist = ssh.run("ls -d " + models_dir + "/Qwen3-TTS-* 2>/dev/null | wc -l", timeout=30)
            count = (exist.strip().splitlines() or ["0"])[-1]
            self._ok(job, "S4B", "已跳过下载；数据盘 " + models_dir + " 现有 " + str(count) + " 个模型目录")
            return
        names = ["Base", "CustomVoice", "VoiceDesign"] if size == "1.7B" else ["Base", "CustomVoice"]
        check = " && ".join(
            "[ -d " + models_dir + "/Qwen3-TTS-12Hz-" + size + "-" + n + " ]" for n in names
        )
        rc, out = ssh.run("cd " + remote_dir + " && " + check + " && echo models_ok || echo models_missing", timeout=60)
        if "models_ok" in out:
            self._ok(job, "S4B", "模型已存在，跳过下载")
            return
        self._log(job, "云端缺少模型（" + size + "），自动下载到 " + models_dir + "（约 12.6GB，ModelScope 国内源）")
        rc, out = ssh.run(
            "cd " + remote_dir + " && mkdir -p logs " + models_dir + " && (nohup .venv/bin/python scripts/download_models.py --size "
            + size + " --out " + models_dir + " > logs/download.log 2>&1 & echo $! > logs/download.pid) && sleep 2 && cat logs/download.pid",
            timeout=120,
        )
        pid = out.strip().splitlines()[-1] if out.strip() else ""
        if not pid.isdigit():
            raise AppError("REMOTE_MODEL_DOWNLOAD_FAILED", "启动下载进程失败：" + out[-300:])
        deadline = _now() + 5400
        last = -1
        while _now() < deadline:
            if self._cancel.is_set():
                ssh.run("cd " + remote_dir + " && [ -f logs/download.pid ] && kill $(cat logs/download.pid) 2>/dev/null; true", timeout=30)
                raise AppError("CANCELLED", "任务已取消")
            rc, alive = ssh.run("ps -p " + pid + " -o pid= 2>/dev/null", timeout=20)
            rc, size_out = ssh.run("du -sm " + models_dir + " 2>/dev/null | awk '{print $1}'", timeout=30)
            try:
                mb = int((size_out.strip().splitlines() or ["0"])[-1])
            except ValueError:
                mb = 0
            if mb != last:
                last = mb
                pct = min(99, int(mb / 13000 * 100))
                self._log(job, "模型下载中：" + str(mb) + "MB / 约 13000MB（" + str(pct) + "%）")
                self._emit(job, "progress", {"step": "S4B", "percent": pct / 100.0, "message": str(mb) + "MB"})
            if not alive.strip():
                break
            time.sleep(15)
        rc, out = ssh.run("cd " + remote_dir + " && " + check + " && echo models_ok || echo models_missing", timeout=60)
        if "models_ok" not in out:
            rc, tail = ssh.run("cd " + remote_dir + " && tail -n 15 logs/download.log 2>/dev/null", timeout=30)
            raise AppError("REMOTE_MODEL_DOWNLOAD_FAILED", "模型下载未完成：" + tail[-500:])
        rc, total = ssh.run("du -sm " + models_dir + " | awk '{print $1}'", timeout=30)
        self._ok(job, "S4B", "模型就绪（" + total.strip() + "MB）")

    def _s5_start(self, job, payload, ssh, ctx) -> None:
        opts = job.get("options") or {}
        remote_dir = job["remote"].get("path") or "~/qwentts"
        remote_port = int(opts.get("remote_port") or 8443)
        api_key = uuid.uuid4().hex
        self._pending_key = api_key
        from .secretstore import protect

        enc = protect(api_key)
        if enc:
            job["remote"]["api_key_enc"] = enc
        env = job.get("env") or {}
        data_root = env.get("data_dir") or ""
        models_dir = job["remote"].get("models_dir") or (remote_dir + "/models")
        storage_yaml = ""
        if data_root:
            storage_yaml = (
                "storage:\n  models_dir: " + models_dir + "\n  data_dir: " + data_root + "/qwentts-data\n"
            )
        remote_yaml = (
            "run:\n  mode: local\n"
            "server:\n  host: 0.0.0.0\n  port: " + str(remote_port) + "\n"
            + storage_yaml +
            "cloud:\n  api_key: " + api_key + "\n  verify_tls: false\n"
        )
        tmp = Path(self.cfg_manager.app.data_dir) / "cloud-agent" / "remote-config.yaml"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(remote_yaml, encoding="utf-8")
        try:
            rc, out = ssh.run("mkdir -p " + remote_dir + "/config " + remote_dir + "/logs", timeout=30)
            ssh.put(tmp, remote_dir + "/config/config.yaml")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        rc, out = ssh.run("cd " + remote_dir + " && cat logs/app.pid 2>/dev/null", timeout=30)
        pid = out.strip().splitlines()[-1] if out.strip() else ""
        # 总是重启：复用旧进程会让「旧代码 / 旧配置」继续跑，导致后续探测永远对不上
        if pid.isdigit():
            ssh.run("kill " + pid + " 2>/dev/null; true", timeout=30)
        ssh.run(
            "pkill -f 'app.main --host 0.0.0.0 --port " + str(remote_port) + "' 2>/dev/null; sleep 2; true",
            timeout=30,
        )
        if pid.isdigit():
            self._log(job, "已停止云端旧进程 pid=" + pid + "，准备用当前代码重启")
        launch = (
            "cd " + remote_dir + "/backend && setsid nohup ../.venv/bin/python -m app.main "
            "--host 0.0.0.0 --port " + str(remote_port) + " > ../logs/app.log 2>&1 < /dev/null & echo launched"
        )
        # 后台进程会占着 SSH 通道，exec_command 可能一直等 EOF —— 只要起来了就算成功
        try:
            ssh.run(launch, timeout=20)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(12)
        rc, out = ssh.run(
            "pgrep -f 'app.main --host 0.0.0.0 --port " + str(remote_port) + "' | head -1", timeout=20
        )
        pid = out.strip().splitlines()[-1] if out.strip() else ""
        if not pid.isdigit():
            raise AppError("REMOTE_START_FAILED", "启动云端服务失败：" + out[-300:])
        rc, alive2 = ssh.run("sleep 8; ps -p " + pid + " -o pid= 2>/dev/null", timeout=40)
        if not alive2.strip():
            rc, tail = ssh.run("tail -n 20 " + remote_dir + "/logs/app.log 2>/dev/null", timeout=30)
            raise AppError("REMOTE_START_FAILED", "云端服务启动后立刻退出：" + tail[-500:])
        job["remote"].update({"port": remote_port, "pid": pid})
        self._ok(job, "S5", "pid=" + pid + "（进程存活）")

    def _s6_ready(self, job, payload, ssh, ctx) -> None:
        opts = job.get("options") or {}
        remote_port = int(opts.get("remote_port") or 8443)
        api_key = getattr(self, "_pending_key", "") or _unprotect(job["remote"].get("api_key_enc", ""))
        pid = str(job.get("remote", {}).get("pid") or "")
        if pid:
            rc, alive = ssh.run("ps -p " + pid + " -o pid= 2>/dev/null", timeout=20)
            if not alive.strip():
                rc, tail = ssh.run("tail -n 20 " + (job["remote"].get("path") or "~/qwentts") + "/logs/app.log 2>/dev/null", timeout=30)
                raise AppError("CLOUD_NOT_READY", "云端服务进程已退出（pid=" + pid + "）：" + tail[-400:])
        topology, base_url, ready, detail = self._probe_ready(
            job, remote_port, api_key, llm=getattr(self, "_active_llm", None)
        )
        if not ready:
            raise AppError("CLOUD_NOT_READY", detail or "云端服务未能就绪（可能模型未下载或端口不通）")
        self._pending_key = api_key
        job["result"].update({"topology": topology, "base_url": base_url})
        self._ok(job, "S6", topology + " " + base_url)

    def _s7_local_config(self, job, payload, ssh, ctx) -> None:
        result = job["result"]
        api_key = getattr(self, "_pending_key", "") or _unprotect(job["remote"].get("api_key_enc", ""))
        remote_port = int((job.get("options") or {}).get("remote_port") or 8443)
        try:
            self.cfg_manager.update_runtime({
                "run": {"mode": "cloud"},
                "cloud": {
                    "base_url": result.get("base_url", ""),
                    "api_key": api_key,
                    "verify_tls": False,
                    "tunnel": {
                        # local-forward 说明实际是通过本地映射端口访问的（就是我们自己起的隧道），
                        # 也必须把 tunnel.enabled 打开，否则 S8 用新配置再也连不上
                        "enabled": (
                            result.get("topology") == "tunnel"
                            or (result.get("topology") == "local-forward"
                                and "127.0.0.1" in str(result.get("base_url") or ""))
                        ),
                        "local_port": int((job.get("options") or {}).get("local_port") or 18443),
                        "ssh_host": job["target"]["host"],
                        "ssh_port": job["target"]["ssh_port"],
                        "username": job["target"]["username"],
                        "key_path": (payload.get("auth") or {}).get("key_path", ""),
                        "password_enc": _protect((payload.get("auth") or {}).get("password", "")),
                        "remote_port": remote_port,
                    },
                },
            })
        except Exception as exc:
            raise AppError("CONFIG_WRITE_FAILED", str(exc)) from exc
        result["api_key_set"] = True
        if callable(self.on_applied):
            try:
                self.on_applied()
            except Exception:  # noqa: BLE001
                logger.debug("写入配置后热应用失败", exc_info=True)
        self._ok(job, "S7", "run.mode=cloud 已生效")

    def _s8_verify(self, job, payload, ssh, ctx) -> None:
        latency = self._local_probe()
        if latency is None:
            raise AppError("VERIFY_FAILED", "本地按新配置访问云端失败，请查看日志")
        job["result"]["latency_ms"] = latency
        self._ok(job, "S8", "本地探活成功 " + str(latency) + "ms")

    # ---------------- AI 自主排障 ----------------
    def _audit(self, job: dict, turn: int, tool: str, args: dict, result: str) -> None:
        path = self.store.dir.parent / "audit.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": round(_now(), 3),
                "job_id": job["job_id"],
                "turn": turn,
                "tool": tool,
                "args": redact(json.dumps(args, ensure_ascii=False))[:300],
                "result": redact(result)[:300],
            },
            ensure_ascii=False,
        )
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.debug("写审计日志失败", exc_info=True)

    def _ai_enabled(self, job: dict, llm: "LLMClient") -> bool:
        return bool((job.get("options") or {}).get("ai_mode")) and llm.available()

    def _exec_tool(self, job: dict, ssh: "_SSH", name: str, args: dict) -> str:
        if name == "run_step":
            target = str(args.get("step_id") or "")
            current = str(job.get("current_step") or "")
            if target and target != current:
                return "error: 只能重跑当前失败步骤（" + current + "），不能跳步"
            return "ok: 将重跑当前失败步骤 " + current
        if name == "give_up":
            return "ok"
        if name != "remote_exec":
            return "error: 未知工具 " + str(name)
        cmd_id = str(args.get("cmd_id") or "")
        argv = args.get("args") or {}
        remote_dir = job.get("remote") or {}
        base = remote_dir.get("path") or "~/qwentts"
        if ssh is None or getattr(ssh, "_client", None) is None:
            return "error: SSH 未连接，无法执行远端命令"
        if not isinstance(argv, dict):
            return "error: args 必须是对象"
        try:
            if cmd_id == "env_probe":
                rc, out = ssh.run(
                    "python3 --version 2>&1; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1; "
                    "df -m ~ | tail -1 | awk '{print $4}'; . /etc/os-release 2>/dev/null; echo os=$PRETTY_NAME", timeout=60)
            elif cmd_id == "check_port":
                port = int(argv.get("port") or remote_dir.get("port") or 8443)
                rc, out = ssh.run("ss -ltn 2>/dev/null | grep ':" + str(port) + " ' || echo port_free", timeout=30)
            elif cmd_id == "tail_log":
                lines = max(1, min(int(argv.get("lines") or 40), 200))
                rc, out = ssh.run("cd " + base + " && tail -n " + str(lines) + " logs/app.log 2>/dev/null || echo no_log", timeout=30)
            elif cmd_id == "disk_free":
                rc, out = ssh.run("df -h ~ | tail -1", timeout=30)
            elif cmd_id == "pip_sync":
                index = str(argv.get("index") or "https://pypi.tuna.tsinghua.edu.cn/simple")
                if not index.startswith("http"):
                    return "error: index 必须是 http(s) 地址"
                rc, out = ssh.run("cd " + base + " && .venv/bin/pip install -q -r requirements.txt -i " + index + " && echo pip_ok", timeout=1800)
            elif cmd_id == "start_service":
                port = int(argv.get("port") or remote_dir.get("port") or 8443)
                rc, out = ssh.run(
                    "cd " + base + " && (nohup .venv/bin/python -m app.main --host 0.0.0.0 --port " + str(port)
                    + " > logs/app.log 2>&1 & echo $! > logs/app.pid) && sleep 2 && cat logs/app.pid", timeout=120)
            elif cmd_id == "stop_service":
                rc, out = ssh.run("cd " + base + " && [ -f logs/app.pid ] && kill $(cat logs/app.pid) 2>/dev/null; rm -f logs/app.pid; echo stopped", timeout=60)
            elif cmd_id == "write_remote_config":
                key = str(argv.get("key") or "")
                value = str(argv.get("value") or "")
                if not key or not value:
                    return "error: 需要 key 与 value"
                rc, out = ssh.run(
                    "cd " + base + " && .venv/bin/python scripts/remote_config_patch.py --set "
                    + shlex.quote(key + "=" + value), timeout=120)
                return redact(out)[:1200] or "(无输出)"
            elif cmd_id == "restart_service":
                port = int(argv.get("port") or remote_dir.get("port") or 8443)
                ssh.run("cd " + base + " && [ -f logs/app.pid ] && kill $(cat logs/app.pid) 2>/dev/null; sleep 2; true", timeout=60)
                rc, out = ssh.run(
                    "cd " + base + "/backend && (nohup ../.venv/bin/python -m app.main --host 0.0.0.0 --port "
                    + str(port) + " > ../logs/app.log 2>&1 & echo $! > ../logs/app.pid) && sleep 3 && cat ../logs/app.pid",
                    timeout=120)
                pid = out.strip().splitlines()[-1] if out.strip() else ""
                if not pid.isdigit():
                    return "error: 重启失败 " + redact(out)[:300]
                job["remote"]["pid"] = pid
                return "ok: 已重启，pid=" + pid
            elif cmd_id == "read_remote_file":
                rel = str(argv.get("path") or "")
                if not rel or ".." in rel or rel.startswith("/"):
                    return "error: 只允许读取项目目录内的相对路径"
                rc, out = ssh.run("cd " + base + " && head -n 80 " + rel + " 2>/dev/null || echo no_file", timeout=30)
            else:
                return "error: 未注册的命令 " + cmd_id
            return redact(out)[:2000] or "(空输出)"
        except Exception as exc:  # noqa: BLE001
            return "error: " + redact(str(exc))[:300]

    def _ai_troubleshoot(self, job: dict, ssh: "_SSH", llm: "LLMClient", step: str, code: str, message: str) -> bool:
        if not self._ai_enabled(job, llm):
            return False
        models = llm.list_models()
        if models and llm.model not in models:
            self._log(job, "AI 排障跳过：模型 " + llm.model + " 不在可用列表（可用：" + "、".join(models[:6]) + "）")
            return False
        messages = [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "步骤 " + step + " 失败。错误码：" + code + "\n信息：" + redact(message)[:800]
                + "\n环境摘要：" + json.dumps(job.get("env") or {}, ensure_ascii=False)[:800],
            },
        ]
        for turn in range(max(1, llm.max_turns)):
            if self._cancel.is_set():
                return False
            resp = llm.chat(messages, AGENT_TOOLS)
            if not resp:
                self._log(job, "AI 排障中断：模型不可用，回退确定性流程")
                return False
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                text = redact(msg.get("content") or "")[:200]
                self._log(job, "AI 排障结论：" + (text or "无可执行动作"))
                return False
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "give_up":
                    reason = redact(str(args.get("reason") or ""))[:300]
                    self._log(job, "AI 判定无法自动修复：" + reason)
                    return False
                result = self._exec_tool(job, ssh, name, args if isinstance(args, dict) else {})
                self._audit(job, turn, name, args if isinstance(args, dict) else {}, result)
                self._log(job, "AI[" + name + "] " + result[:160].replace("\n", " | "))
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result[:1500]})
                if name == "run_step":
                    job["result"]["ai_step_retry"] = args.get("step_id")
                    return True
        self._log(job, "AI 排障达到轮次上限，回退确定性流程")
        return False

    # ---------------- 辅助 ----------------
    @staticmethod
    def _parse_env(out: str, remote_port: int) -> dict:
        env: dict = {"port_busy": False}
        for line in out.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().upper(), value.strip()
            if key == "PY":
                env["python_bin"] = value
            elif key == "PYVER":
                env["python"] = value
            elif key == "GPU":
                env["gpu"] = value
            elif key == "DISK":
                try:
                    env["disk_free_mb"] = int(value)
                except ValueError:
                    pass
            elif key == "PORTBUSY":
                env["port_busy"] = value not in ("0", "")
            elif key == "FFMPEG":
                env["ffmpeg"] = value.lower() == "yes"
            elif key == "OS":
                env["os"] = value
            elif key == "DATA_DIR":
                env["data_dir"] = "" if value in ("none", "") else value
            elif key == "DATA_DISK":
                try:
                    env["data_free_mb"] = int(value)
                except ValueError:
                    pass
        return env

    def _build_tarball(self) -> Path:
        root = Path(__file__).resolve().parents[2]
        skip = {"models", "data", ".cache", "node_modules", "__pycache__", "webui", ".git"}
        tmp = Path(self.cfg_manager.app.data_dir) / "cloud-agent" / "qwentts-src.tar.gz"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tmp, "w:gz") as tar:
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root)
                if set(rel.parts[:-1]) & skip or path.suffix in (".pyc", ".log", ".db"):
                    continue
                if path.is_file():
                    tar.add(path, arcname=rel.as_posix())
        return tmp

    def _probe_ready(self, job: dict, port: int, api_key: str, llm: "LLMClient | None" = None) -> tuple[str, str, bool, str]:
        """先试直连；直连不通时自动起 SSH 隧道再试（云厂商常不开公网端口）。"""
        host = job["target"]["host"]
        cred = self._cred.get(job["job_id"], {})
        probe_budget = int((job.get("options") or {}).get("probe_timeout_s") or 60)
        started = _now()
        direct_deadline = started + probe_budget
        synth_tries = 0
        attempt = 0
        last_note = 0.0
        self._note(job, "S6", "开始探测（直连，预算 " + str(probe_budget) + "s）")
        detail = ""
        not_ready_streak = 0
        ai_handoffs = 0
        while _now() < direct_deadline:
            if self._cancel.is_set():
                raise AppError("CANCELLED", "任务已取消")
            attempt += 1
            allow = synth_tries < 2
            ok, detail = self._check("https://" + host + ":" + str(port), api_key, allow_synth=allow)
            if _now() - last_note >= 3:
                last_note = _now()
                self._note(
                    job, "S6",
                    "直连探测第 " + str(attempt) + " 次，已 " + str(int(_now() - started)) + "s："
                    + (detail or "未就绪"),
                )
            if not ok and detail and "推理探测" in detail:
                synth_tries += 1
            if not ok and "模型未就绪" in (detail or ""):
                not_ready_streak += 1
                if llm is not None and self._ai_enabled(job, llm) and not_ready_streak >= 3 and ai_handoffs < 2:
                    ai_handoffs += 1
                    not_ready_streak = 0
                    self._log(job, "S6 连续未就绪，交给 AI 排障（第 " + str(ai_handoffs) + " 次）")
                    if self._ai_troubleshoot(job, self._active_ssh, llm, "S6", "CLOUD_NOT_READY", detail):
                        time.sleep(10)
                        continue
            if ok:
                return "direct", "https://" + host + ":" + str(port), True, ""
            time.sleep(5)

        # 直连不通 → 先看用户是否已用官方隧道工具把端口映射到本地
        local_port = int((job.get("options") or {}).get("local_port") or 18443)
        ok, d_local = self._check("https://127.0.0.1:" + str(local_port), api_key, allow_synth=synth_tries < 2)
        if ok:
            return "local-forward", "https://127.0.0.1:" + str(local_port), True, ""
        ok, d_local_http = self._check("http://127.0.0.1:" + str(local_port), api_key)
        if ok:
            return "local-forward", "http://127.0.0.1:" + str(local_port), True, ""
        tunnel = self._start_tunnel(job, port, local_port, cred)
        if tunnel is not None:
            base = "https://127.0.0.1:" + str(local_port)
            self._log(job, "直连不通，已建立 SSH 隧道 127.0.0.1:" + str(local_port) + " → " + host + ":" + str(port))
            deadline = _now() + 120
            self._note(job, "S6", "直连超时，改走 SSH 隧道 127.0.0.1:" + str(local_port))
            while _now() < deadline:
                if self._cancel.is_set():
                    raise AppError("CANCELLED", "任务已取消")
                ok, d2 = self._check(base, api_key)
                if _now() - last_note >= 3:
                    last_note = _now()
                    self._note(job, "S6", "隧道探测，已 " + str(int(_now() - started)) + "s：" + (d2 or "未就绪"))
                if ok:
                    return "tunnel", base, True, ""
                detail = d2 or detail
                time.sleep(5)
        return "direct", "https://" + host + ":" + str(port), False, detail or "直连与隧道均未就绪"

    def _start_tunnel(self, job: dict, remote_port: int, local_port: int, cred: dict):
        try:
            from .config import TunnelCfg
            from .tunnel import SSHTunnel

            cfg = TunnelCfg(
                enabled=True,
                ssh_host=job["target"]["host"],
                ssh_port=int(job["target"]["ssh_port"]),
                username=job["target"]["username"],
                key_path=cred.get("key_path", ""),
                password=cred.get("password", ""),
                local_port=local_port,
                remote_host="127.0.0.1",
                remote_port=remote_port,
            )
            tunnel = SSHTunnel(cfg)
            status = tunnel.start()
            self._tunnels[job["job_id"]] = tunnel
            self._log(job, "隧道状态：" + json.dumps({k: status.get(k) for k in ("running", "local_port", "error")}, ensure_ascii=False))
            return tunnel if status.get("running") else None
        except Exception as exc:  # noqa: BLE001
            self._log(job, "建立隧道失败：" + redact(str(exc))[:160])
            return None

    def _check(self, base_url: str, api_key: str, allow_synth: bool = True) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=5.0, verify=False) as client:
                r = client.get(base_url + "/v1/health", headers={"X-API-Key": api_key})
                if r.status_code != 200:
                    return False, "健康检查返回 " + str(r.status_code)
                body = r.json()
                if body.get("status") != "ok":
                    return False, "健康检查状态异常：" + str(body.get("status"))
                if not body.get("ready"):
                    present = body.get("models_present") or []
                    want = body.get("model") or ""
                    hint = "已就绪模型：" + ("、".join(present) if present else "无")
                    return False, (
                        "服务已启动但模型未就绪（ready=false）：期望 " + str(want) + "；" + hint
                        + "。请检查云端 config.yaml 的 storage.models_dir 是否指向 S4B 下载目录"
                    )
                if not allow_synth:
                    return True, ""
                probe = client.post(
                    base_url + "/v1/engine/synth",
                    headers={"X-API-Key": api_key},
                    json={"kind": "tts", "text": "你好", "language": "Auto", "params": {}},
                    timeout=45.0,
                )
                if probe.status_code in (200, 206) and len(probe.content) > 1000:
                    return True, ""
                if probe.status_code in (200, 206):
                    return False, "推理探测返回内容过短（" + str(len(probe.content)) + " 字节）"
                body_text = ""
                try:
                    body = probe.json()
                    body_text = str((body.get("detail") or {}).get("detail") or (body.get("detail") or {}).get("message") or body)[:220]
                except Exception:  # noqa: BLE001
                    body_text = probe.text[:220]
                return False, "推理探测失败：HTTP " + str(probe.status_code) + " " + body_text
        except Exception as exc:  # noqa: BLE001
            return False, "连接失败：" + type(exc).__name__

    def _local_probe(self) -> int | None:
        cfg = self.cfg_manager.app
        cloud = cfg.cloud
        url = cloud.effective_base_url()
        headers = {"X-API-Key": cloud.api_key} if cloud.api_key else {}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=20.0, verify=cloud.verify_tls) as client:
                r = client.get(url + "/v1/health", headers=headers)
                if r.status_code == 200 and r.json().get("ready"):
                    return int((time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001
            return None
        return None
