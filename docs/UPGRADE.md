# 升级工具（独立 exe）

`tools/upgrader/dist/QWENTTS-Upgrader.exe` —— 双层 GUI，不需要启动主程序、不依赖项目代码。

## 用法

1. 把新版本压缩包解压到任意目录（如 `E:\QWENTTS-portable-1.1.18`）
2. 双击 `QWENTTS-Upgrader.exe`
3. 「当前安装目录」= 你现在正在用的目录（含 `backend`、`VERSION.json`）
4. 「新版本目录」= 刚解压出来的目录
5. 点 **① 预检** → 看版本对比、将覆盖/新增文件数、保留目录、风险提示
6. 点 **② 开始升级** → 自动备份 → 复制 → 完成
7. 关掉旧的服务窗口，重新双击 `start.bat` 启动
8. 出问题就点 **回滚…**，选刚才那个备份还原

## 保留 / 覆盖规则

| 永不覆盖 | 会覆盖 | 只新增不删 |
| --- | --- | --- |
| `data/`（数据库、任务、音色、证书） | `backend/`、`frontend/dist/`、`scripts/`、`docs/`、`launcher/dist/` | 新版本里没有的旧文件保持不动 |
| `models/`（12.6GB 权重） | `requirements*.txt`、`VERSION.json`、`start.*` 等 | |
| `config/config.yaml`（你的配置） | | |
| `.cache/`、`.venv/`、`logs/` | | |

## 安全机制

- 升级前把「将被覆盖的旧文件」打包到 `data/upgrade/backup-<版本>-<时间>.zip`
- 复制过程中任何异常 → **自动回滚**并报错，环境不变
- 每次升级记录在 `data/upgrade/history.json`
- 有任务在跑时默认拦住（勾选「强制」才继续）

## 命令行（排障用）

```bat
QWENTTS-Upgrader.exe --selftest
QWENTTS-Upgrader.exe --cli "E:\QWENTTS" "E:\QWENTTS-portable-1.1.18"
QWENTTS-Upgrader.exe --cli "E:\QWENTTS" "E:\QWENTTS-portable-1.1.18" --apply
QWENTTS-Upgrader.exe --rollback "E:\QWENTTS" "E:\QWENTTS\data\upgrade\backup-1.1.12-xxx.zip"
```
