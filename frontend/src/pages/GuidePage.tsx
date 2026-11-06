import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Cloud,
  ArrowUp,
  ArrowUpRight,
  AudioLines,
  BadgeCheck,
  BookOpen,
  Cpu,
  Database,
  LifeBuoy,
  ListChecks,
  PlugZap,
  Search,
  ServerCog,
  Sparkles,
  Terminal,
  Timer,
  Users,
  X,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { InlineNote, SectionCard } from '../components/ui';

type SectionId =
  | 'start'
  | 'mode'
  | 'cloud'
  | 'tunnel'
  | 'asr'
  | 'diarize'
  | 'warmup'
  | 'engines'
  | 'cloudEngines'
  | 'local'
  | 'qc'
  | 'text'
  | 'post'
  | 'export'
  | 'duration'
  | 'batch'
  | 'mcp'
  | 'mcpConnect'
  | 'defaults'
  | 'license'
  | 'errors';

type GuideField = { name: string; desc: string; suggest?: string };
type GuideFaq = { q: string; a: string };
/** 可直接复制的配置片段（JSON / 命令行） */
type GuideSnippet = { title: string; code: string; note?: string };

type GuideSection = {
  id: SectionId;
  label: string;
  icon: LucideIcon;
  summary: string;
  /** 该分区在设置页的锚点（用于「跳到设置」），空则不显示按钮 */
  settingsAnchor?: string;
  steps?: string[];
  fields?: GuideField[];
  tips?: string[];
  faq?: GuideFaq[];
  snippets?: GuideSnippet[];
};

/**
 * 教程内容。`fields[].name` 必须与设置页 `Field label` 逐字一致，
 * 这样「教程里搜同一个词」和「设置里搜同一个词」能命中同一分区。
 */
const GUIDE: GuideSection[] = [
  {
    id: 'start',
    label: '五分钟跑通',
    icon: BookOpen,
    summary: '第一次使用的推荐顺序：选引擎 → 装模型 → 预热 → 合成 → 看日志',
    steps: [
      '「设置 → 引擎与模型」：默认引擎是 Qwen3-TTS 1.7B。显卡只有 2–3GB 就点 0.6B 那张卡右边的「安装」，再点「切换」。',
      '等安装跑完（会创建引擎专属 venv 并下载模型，国内走 ModelScope 镜像，不占用主环境 torch）。',
      '「设置 → 模型预热」勾选「语音生成」，点「预热选中」——把权重提前装进显存，之后第一个请求就不用等。',
      '「工作台」左侧写文本、选音色，点「开始合成」。任务进度在「任务中心」，生成结果在右侧播放器里。',
      '出错就看「状态与日志」：错误码 + 中文处理建议 + 原始日志都在那一页；也可以在「教程 → 常见错误速查」里按错误码查。',
    ],
    tips: [
      '本项目不会自动下载任何模型：所有模型都要你手动点「安装」，或者按文档给的链接自己下好放进 models/ 目录。',
      '也不会自动安装 Python / Node / 显卡驱动：缺环境时界面会明确提示缺什么，由你决定装不装。',
    ],
  },
  {
    id: 'mode',
    label: '运行模式',
    icon: ServerCog,
    summary: '决定任务交给「本机显卡」还是「云端 GPU」，改完立即生效',
    settingsAnchor: 'mode',
    fields: [
      {
        name: '运行模式',
        desc: '仅本地 / 仅云端 / 混合。仅本地全部用本机显卡；仅云端全部转发到云端服务；混合按任务类型与显存情况自动选址。',
        suggest: '有可用云端就选「混合」，否则「仅本地」',
      },
      {
        name: '混合模式长文本阈值',
        desc: '混合模式下，超过该字数的克隆任务优先交给云端，避免本机显存爆掉。默认 300 字，可填 50–5000。',
        suggest: '300（4GB 卡可调到 150）',
      },
      {
        name: '混合模式 ASR 优先位置',
        desc: '混合模式下语音转文字（参考音频转写、内容质检回译）先试哪边：本地优先或云端优先。',
        suggest: '本地优先（省流量，小模型够用）',
      },
    ],
    tips: [
      '切换运行模式是热生效的，不需要重启服务；当前真正生效的模式和后端显示在本卡片下方，也在「状态与日志」里。',
      '「仅云端」时如果云端地址没填或密钥不对，所有任务都会失败在 CLOUD_UNREACHABLE / CLOUD_AUTH_FAILED，这时先切回「仅本地」。',
      '混合模式的判断顺序是：先看显存够不够，再看字数阈值，都不会自动改你选的模式。',
    ],
    faq: [
      {
        q: '混合模式会不会把我的音频偷偷传到云端？',
        a: '只在该任务被判定要走云端时才上传，且只上传参考音频与文本；音色库与生成结果始终存在本机 data/ 目录。不想上传就用「仅本地」。',
      },
    ],
  },
  {
    id: 'cloud',
    label: '云端连接',
    icon: Zap,
    summary: '把任务发给租来的云 GPU；含连通性探测与一键关机省费用',
    settingsAnchor: 'cloud',
    steps: [
      '在云平台（AutoDL / 阿里云 / 腾讯云）按 docs/CLOUD_ACCESS_TUTORIAL.md 起好服务，拿到公网地址与开放端口。',
      '把「服务地址」填成 https://<公网地址>:8443，「API 密钥」填服务端的 X-API-Key。',
      '点「测试连接」——浏览器会直连 <服务地址>/v1/health，8 秒内返回延迟就说明通了。',
      '把「运行模式」切到「仅云端」或「混合」，提交一个短任务验证首包时间。',
      '不用了点「一键关机」，选「关机（省费用）」或「仅停止服务（不关机）」。',
    ],
    fields: [
      {
        name: '服务地址',
        desc: '云端服务的 Base URL，必须以 http:// 或 https:// 开头并带端口。走 SSH 隧道时填隧道后的本地地址。',
        suggest: 'https://<公网IP>:8443 或隧道下 https://127.0.0.1:18443',
      },
      {
        name: 'API 密钥',
        desc: '云端强制鉴权，请求头 X-API-Key。界面上始终脱敏显示，留空表示沿用已保存的值。',
        suggest: '与云端 .env 里 API_KEY 一致',
      },
      {
        name: '校验 TLS 证书',
        desc: '后端请求云端时是否校验证书。「不校验」只对后端生效；浏览器不受此开关影响。',
        suggest: '生产「校验」；自签证书调试「不校验」',
      },
      {
        name: '空闲自动停机',
        desc: '开启后，云端/混合模式下空闲超过设定时长会自动让云端实例停机，省费用。默认关闭。',
        suggest: '关闭（除非经常忘记关机）',
      },
      {
        name: '空闲时长（分钟）',
        desc: '空闲多久触发自动停机，0 表示不停机，上限 240。',
        suggest: '15',
      },
      {
        name: '云端 Base 模型 / 云端 CustomVoice 模型 / 云端 VoiceDesign 模型',
        desc: '云端侧使用的模型标识，必须与云端实际加载的目录名一致，否则云端报模型不存在。',
        suggest: 'Qwen3-TTS-12Hz-1.7B-Base / -CustomVoice / -VoiceDesign',
      },
      {
        name: '测试连接',
        desc: '纯前端只读探测：直接 GET <服务地址>/v1/health，8 秒超时，不写配置、不影响正在跑的任务。',
        suggest: '改完地址先点这个',
      },
      {
        name: '一键关机',
        desc: '通过 SSH 让云端实例执行关机（shutdown -h now），平台上会停止计费；弹窗里可改选「仅停止服务」只杀推理进程。',
        suggest: '每天用完点一次',
      },
    ],
    tips: [
      '「一键关机」会让云端正在跑的任务立即中断且无法恢复，本地 SSH 隧道也会一起断开。',
      '关机后模型与数据仍在云端数据盘，但再次使用必须去平台控制台「开机」，不是点本页面就能唤醒。',
      '一键关机用的是「SSH 隧道」那一组 SSH 主机/端口/账号密码，所以要先在隧道分区把账号填对。',
      '浏览器打开的是 https 页面时，请求 http:// 的云地址会被当成混合内容拦截——这时要么用 https，要么改用 SSH 隧道。',
    ],
    faq: [
      {
        q: '点「测试连接」失败，但云端明明是好的？',
        a: '浏览器没法跳过证书校验。自签证书请在浏览器里先手动访问一次 <服务地址>/v1/health 并接受证书，或者改用 SSH 隧道 + https://127.0.0.1:18443。',
      },
      {
        q: '返回 401 / 403？',
        a: '服务可达但密钥不对，改「API 密钥」（大写 X-API-Key）。',
      },
    ],
  },
  {
    id: 'tunnel',
    label: 'SSH 隧道',
    icon: PlugZap,
    summary: '把云端 8443 映射到本机端口，绕开自签证书与端口暴露问题',
    settingsAnchor: 'tunnel',
    steps: [
      '「启用隧道」选开启。',
      '填 SSH 主机（云平台给的连接域名）、SSH 端口（如 29943）、用户名（如 root）。',
      '认证二选一：填「私钥路径」，或填「密码」。',
      '本地映射端口填 18443，远端地址 / 端口保持 127.0.0.1 / 8443。',
      '回到「云端连接」，把服务地址改成 https://127.0.0.1:18443，再点「测试连接」。',
    ],
    fields: [
      { name: '启用隧道', desc: '开启后本机监听 local_port，把流量转发到云端的远端地址与端口。', suggest: '云端连不上时开启' },
      { name: 'SSH 主机', desc: '云平台给的外网连接域名，不要带 ssh:// 前缀。', suggest: 'connect.nmb2.seetacloud.com' },
      { name: 'SSH 端口', desc: '云平台分配的 SSH 端口，不是 22 也正常。', suggest: '以平台控制台为准' },
      { name: '用户名', desc: 'SSH 登录名。', suggest: 'root' },
      { name: '私钥路径', desc: '本机私钥文件的绝对路径；留空则改用密码登录。', suggest: '留空用密码' },
      { name: '密码', desc: 'SSH 密码，明文保存在 data/runtime.json，请自行控制该目录权限。', suggest: '平台给的登录密码' },
      { name: '本地映射端口', desc: '本机监听的端口，服务地址要填同一个值。', suggest: '18443（避开 8443 本机服务）' },
      { name: '远端地址 / 端口', desc: '云端服务的实际监听地址与端口。', suggest: '127.0.0.1 / 8443' },
    ],
    tips: [
      '本机自己也在 8443 上跑服务，所以本地映射端口别填 8443，否则端口冲突。',
      '隧道状态在分区的提示条与「状态与日志」里都能看到；显示未运行时云端地址就是不可达的。',
      'WinError 10061「目标计算机积极拒绝」＝本地端口没人监听：隧道没起，或云端已关机。',
      '用平台自带隧道（如 AutoDL 的自定义服务）时，把「代理到本地端口」填 18443、「代理到远程端口」填 8443。',
    ],
  },
  {
    id: 'asr',
    label: '语音转文字',
    icon: Database,
    summary: '参考音频自动转写 + 内容质检回译都靠它',
    settingsAnchor: 'asr',
    fields: [
      {
        name: 'ASR 引擎',
        desc: '本地（faster-whisper，离线免费）或云端接口。混合模式下最终由「ASR 优先位置」决定顺序。',
        suggest: '本地',
      },
      {
        name: '本地模型规模',
        desc: 'tiny / base / small / medium / large-v3 / large-v3-turbo / distil-large-v3。越大越准也越慢。',
        suggest: 'small（中文够用，显存占用小）',
      },
      {
        name: '计算精度',
        desc: 'int8 最省显存，float16 更快但需要显卡支持，float32 最准最慢。',
        suggest: 'int8',
      },
    ],
    tips: [
      '某些引擎（如 VoxCPM）做声音克隆时必须有参考文本，没填就会自动调 ASR 转写——ASR 不可用会直接报 ASR_FAILED。',
      '内容质检也用 ASR 做回译比对，所以两个功能共用这一份配置。',
      '参考音频建议 3–10 秒、无背景音乐，转写准确率会明显提高。',
    ],
    faq: [
      { q: '模型什么时候下载？', a: '第一次用到 ASR 时按需下载到 .cache/，属于模型缓存；离线环境请提前手动放好。' },
    ],
  },
  {
    id: 'diarize',
    label: '区分说话人',
    icon: Users,
    summary: '转写时标出谁在说话（SPK1/SPK2），可改名、可导出带说话人字幕',
    settingsAnchor: 'asr',
    steps: [
      '先到 设置 → 引擎与模型 → 语音识别 安装「CAM++ 声纹」（约 27MB，装完会在 ASR 卡片里出现）。',
      '工作台 → 音频转文字 → 选音频 → 勾上「区分说话人」，用「最多 N 人」限定范围（2-8，会议常用 3-4）。',
      '点「开始转写」，任务日志会依次出现「声纹抽取 1/2 → 聚类分析 → 区分完成」；中途可点取消，任务会落成「已取消」。',
      '结果区按 SPK1/SPK2 彩色分段显示；想改成真名就到 任务中心 → 展开该任务 → 说话人分段 → 改名。',
    ],
    fields: [
      { name: '区分说话人', desc: '任务级开关，优先级高于设置里的全局开关；关掉就走纯文本转写（更快）。', suggest: '需要标注人时打开' },
      { name: '最多', desc: '聚类上限。识别出的簇超过上限时会自动合并最相似的几簇，所以填 2 就会合并成两个人。', suggest: '2-4' },
      { name: '最多说话人', desc: '设置页的全局默认值（2-8），工作台没单独指定时用它。', suggest: '4' },
      { name: '带说话人', desc: '任务中心导出字幕时的复选框：勾选则每句前加 [SPK1] 或改后的姓名，不勾导出纯文本字幕。', suggest: '勾选' },
    ],
    tips: [
      '显存只有 4GB 时，转写用的 whisper 会在任务结束后自动卸载——ASR 与 TTS 同时驻留会撑爆显存导致服务崩溃，这是有意的取舍。',
      '云端转写接口不支持说话人区分；勾了开关但实际回退到云端时，任务日志会明确写「云端转写不支持说话人区分」，不会静默给你一段没有 SPK 的文本。',
      '单人音频不会被硬拆成 N 个人（先用距离阈值自动定簇数，再按上限合并）。',
      '声纹对短片段（< 0.25s）没有把握，这类片段会跟随最近的有效片段。',
    ],
    faq: [
      { q: '勾了开关却没有 SPK 标记？', a: '多半是 CAM++ 没装，或走了云端。看任务日志：缺模型会报 DIARIZE_MODEL_MISSING 并给出安装路径，云端回退会写一句明确提示。' },
      { q: '两个人被识别成一个人？', a: '把「最多」调大，或把设置里的阈值调小（阈值越小越容易被判成不同人）。音色接近、背景噪声大时准确率会下降。' },
    ],
  },
  {
    id: 'warmup',
    label: '模型预热',
    icon: Cpu,
    summary: '提前把权重装进显存/内存，第一个请求就不用等冷启动',
    settingsAnchor: 'warmup',
    steps: [
      '勾选要预热的模型（语音生成最常用，先勾它）。',
      '点「预热选中」，等十几秒看到徽标变成「已加载」。',
      '要释放显存就点「卸载选中」，或点「全部卸载（释放显存）」。',
      '想每次启动都自动装好，就把「启动时自动预热」设为开启，并在「启动预热哪些模型」里勾选。',
    ],
    fields: [
      { name: '语音生成', desc: 'CustomVoice 模型，内置音色 + 指令控制，最常用。', suggest: '必勾' },
      { name: '声音克隆', desc: 'Base 模型，零样本克隆；参考音频编码器会额外占一点显存。', suggest: '要克隆再勾' },
      { name: '音色设计', desc: 'VoiceDesign 模型，用自然语言描述生成音色；只有 1.7B 提供（本地装 1.7B，或把运行模式切云端用云端 1.7B 的 VoiceDesign）。', suggest: '要设计音色再勾' },
      { name: '预热选中', desc: '把勾选的模型加载进显存，返回每项的状态（已加载 / 被挤出显存 / 0.6B 回退 / offload）。', suggest: '启动后点一次' },
      { name: '卸载选中', desc: '只卸载勾选的模型，不影响其它已加载模型。', suggest: '换引擎前先点' },
      { name: '全部卸载（释放显存）', desc: '一次清空所有已加载模型，同时关闭「启动时自动预热」，避免下次启动又被占满。', suggest: '显存不够/要跑别的程序时' },
      { name: '启动时自动预热', desc: '服务启动后是否自动加载 warmup_kinds 里的模型。', suggest: '开启' },
      { name: '启动预热哪些模型', desc: '启动时预热哪些种类，默认只有语音生成；全勾会多占显存。', suggest: '只勾语音生成' },
    ],
    tips: [
      '预热只对 Qwen3-TTS（1.7B / 0.6B）生效；CosyVoice / VoxCPM / IndexTTS 走各自子进程，不受这里控制。',
      '徽标上的「0.6B 回退」表示实际加载的是小模型，「offload」表示部分权重放在内存里，速度会明显变慢。',
      '「被挤出显存」说明同时勾了太多模型，显卡装不下——减少勾选数量即可。',
    ],
  },
  {
    id: 'engines',
    label: '引擎与模型',
    icon: Cpu,
    summary: '七个可选引擎的安装、切换、依赖与体积，一个引擎一个独立环境',
    settingsAnchor: 'engines',
    steps: [
      '选一个引擎卡片，先看「模型体积」和「显存需求」是否符合你的机器。',
      '点「安装」：会创建该引擎专属 venv、按锁文件装依赖、再下载模型（国内镜像）。',
      '装完卡片状态变成「可用」，点「切换」即可切过去；切换时会先卸载旧引擎并清显存。',
      '想看安装做了什么，点「预览」复制命令；依赖缺失时卡片会给带约束文件的修复命令。',
    ],
    fields: [
      { name: '引擎', desc: 'Qwen3-TTS 1.7B（默认）/ 0.6B（小显存）/ CosyVoice3 / CosyVoice2 / CosyVoice-300M / VoxCPM1.5 / IndexTTS2。', suggest: '4GB 以上显卡用 1.7B，2–3GB 用 0.6B' },
      { name: '模型', desc: '该引擎的模型标识与本地目录名，克隆/音色设计等能力由引擎声明，不支持的会灰掉。', suggest: '保持默认' },
      { name: '安装', desc: '创建 <models>/<引擎>/.venv 并按锁文件安装依赖 + 下载权重；不碰主环境 torch。', suggest: '首次使用点一次' },
      { name: '预览', desc: '显示将要执行的 pip 与下载命令，可复制到终端手动执行。', suggest: '排障时用' },
      { name: '切换', desc: '把该引擎设为当前引擎，同时同步本地模型规格（0.6B ↔ qwen3-tts-0.6b）。', suggest: '一次只用一个' },
      { name: '依赖', desc: '该引擎 venv 里的关键包版本是否与锁文件一致；不一致会判定安装失败并给修复命令。', suggest: '—' },
    ],
    tips: [
      '绝不要在主环境直接 pip install 模型包——会替换主环境 torch，本地推理会掉到 CPU（真实事故）。所有命令都应该带 <venv> 的 python 绝对路径与 -c 约束文件。',
      '每个引擎的 venv 互相隔离，VoxCPM 需要 torch 2.5 也只在它自己的 venv 里生效，主环境保持 2.4.1。',
      '能力门禁按「运行模式」判定：纯云端只看云端模型，纯本地只看本机引擎，混合取并集。所以「云端 1.7B + 本地 0.6B + 仅云端」时音色设计依然可用；只有本地模式且本地是 0.6B 才会灰掉。',
      '许可证与商用条款写在各引擎卡片里，商用前请点开确认。',
    ],
  },
  {
    id: 'cloudEngines',
    label: '云端引擎管理',
    icon: Cloud,
    summary: '在本机管理云端主机上的引擎与模型（不占本机磁盘/显存）',
    settingsAnchor: 'cloudEngines',
    steps: [
      '先用「云端连接 → 一键接入云主机」完成部署；成功后会**自动保存**云端主机档案（密码走 DPAPI 加密）。',
      '也可以直接在 设置 → 云端引擎管理 里手工填主机/端口/用户/密码/项目目录/解释器，点「保存并连接」。',
      '点「刷新列表」看云端装了哪些模型——**手工直传上去的模型也会被识别**（状态按云端文件系统实时算）。',
      '要装模型就点该引擎的「安装」，进度与云端日志会实时回显；删除点「删除」，切换云端当前引擎点「设为当前」。',
    ],
    fields: [
      { name: '主机', desc: '云端 SSH 主机名或 IP。', suggest: 'region-42.seetacloud.com' },
      { name: 'SSH 端口', desc: '平台给的端口，不是 22。', suggest: '26322' },
      { name: '用户名', desc: '云端登录用户。', suggest: 'root' },
      { name: '密码', desc: '用 Windows DPAPI 加密存到 data/cloud-host.json，界面不回显；留空表示沿用已保存的密码。', suggest: '—' },
      { name: '云端项目目录', desc: '云端代码所在目录，engines_cli 从这里跑。', suggest: '/root/autodl-tmp/QWENTTS' },
      { name: '云端解释器', desc: '云端 Python 路径（AutoDL 镜像常是 /root/miniconda3/bin/python）。', suggest: '/root/miniconda3/bin/python' },
      { name: '测试连接', desc: '返回 SSH 指纹、云端 Python 版本、项目目录是否存在。', suggest: '先点一次' },
      { name: '安装 / 删除 / 设为当前', desc: '安装走 SSH 调云端 engines_cli（自动挂 hf-mirror）；删除按云端文件算释放体积。', suggest: '—' },
    ],
    tips: [
      '这里的模型在**云端**，不占本机磁盘与显存；本机的模型仍在上方「引擎与模型」里管，两者完全分开。',
      '走 SSH 而不是云端 HTTP：服务没起来时也能装模型（正是「先装模型再起服务」的顺序）。',
      '云端直连 HuggingFace 多半不通，安装时会自动加 HF_ENDPOINT=https://hf-mirror.com；若仍失败，通常是云端 huggingface_hub 版本与镜像不兼容（本次实测 2.2.0 的 httpx 客户端坏掉，降到 0.36.x 正常）。',
      '「未安装」是按云端目录里有没有 model.bin / .onnx 判断的，所以直传上去的文件只要放对位置就会显示「已安装」。',
    ],
    faq: [
      { q: '提示「还没有云端主机档案」？', a: '先去「云端连接 → 一键接入云主机」跑一次，或在本分区手工填主机信息保存。' },
      { q: '安装完成但列表还显示未安装？', a: '模型刚落盘时读到的可能是半成品尺寸，点一次「刷新列表」即可（面板也会在完成后自动再刷一次）。' },
    ],
  },
  {
    id: 'local',
    label: '本地模型',
    icon: Database,
    summary: '显存怎么花：设备、常驻、精度、预留，以及跑不动时的四条退路',
    settingsAnchor: 'local',
    fields: [
      { name: '模型路径', desc: 'Qwen3-TTS 三个模型的本地目录（只读，改路径要编辑 config/config.yaml）。', suggest: 'models/Qwen3-TTS-12Hz-1.7B-*' },
      { name: '推理设备', desc: 'GPU（cuda:0）或 CPU。CPU 功能完整但慢约 25 倍，适合没有可用显卡的机器。', suggest: '2GB 显卡用 CPU，或上云端' },
      { name: '保持模型常驻', desc: '开启则加载后不主动卸载；关闭则空闲到期就释放显存给别的程序。', suggest: '常驻' },
      { name: '显存不足时 CPU offload', desc: '实验特性：把部分权重放到内存里硬加载。当前只能加载、无法正常生成。', suggest: '保持关闭' },
      { name: '允许 0.6B 回退', desc: '开启后 1.7B 装不下会静默降级到 0.6B 并在任务里标注。默认关闭，推荐直接切 0.6B 引擎。', suggest: '禁止' },
      { name: '空闲卸载时长（秒）', desc: '多久没任务就卸载模型，0 表示不按时间卸载，上限 7200。', suggest: '600' },
      { name: '数值精度', desc: 'float16 最省显存；bfloat16 数值更稳但略慢；float32 最稳最占显存。', suggest: '1.7B 用 float16；0.6B 会自动用 bfloat16' },
      { name: '允许显存超额', desc: '允许小幅超额分配；Windows 可回落到共享内存，代价是速度掉很多。', suggest: '开启' },
      { name: '显存预留（GB）', desc: '判定「能不能加载」时先扣掉这部分，留给系统与桌面。', suggest: '0.1（4GB 卡）；2GB 卡可设 0.5' },
    ],
    tips: [
      '路径分区里的三个目录只对 Qwen3-TTS 生效；其它引擎的目录在「引擎与模型」卡片里。',
      '0.6B 的权重约 1.5GB，加上激活与解码缓存，实际需要 ≥2.5GB 空闲显存；只有 2GB 的卡（如 GTX 950）基本跑不动，会报 VRAM_INSUFFICIENT。',
      '跑不动时按顺序试：①切 0.6B 引擎 ②推理设备改 CPU ③缩短单句文本 ④改用云端。',
      '显存不足现在会明确报 VRAM_INSUFFICIENT 并附原始报错，不再只显示「内部错误」。',
      '0.6B 强制 bfloat16，是因为 fp16 在 0.6B 上会采样出 NaN（已实测）。',
    ],
    faq: [
      { q: '为什么我的 2GB 显卡连 0.6B 都不行？', a: '模型权重 1.5GB + 激活/缓存/上下文，峰值会超过 2GB。这不是 bug，是硬件下限；请用 CPU 或云端。' },
    ],
  },
  {
    id: 'qc',
    label: '内容质检',
    icon: BadgeCheck,
    summary: '把生成结果再转写回来，和原文比对，发现漏读错读',
    settingsAnchor: 'qc',
    fields: [
      { name: '自动质检', desc: '开启后每个语音任务完成后会多跑一次本地 ASR 回译，单任务耗时约增加 10–30%。', suggest: '关闭（除非要求逐字准确）' },
      { name: '相似度阈值', desc: '回译文本与原文本的相似度低于该值就标记未通过，范围 0.3–1。', suggest: '0.75' },
    ],
    tips: [
      '质检结果写在任务的「转写文本」字段里，形如 [质检 86%] …回译文本…，不改变任务状态，只是提示。',
      '比对用的是规范化之后的文本，所以数字、单位不会被误判成错误。',
    ],
  },
  {
    id: 'text',
    label: '文本处理',
    icon: Sparkles,
    summary: '合成前把数字、单位、型号、日期改成中文读法，避免读成符号',
    settingsAnchor: 'text',
    fields: [
      { name: '文本规范化', desc: '开启后 4GB→四 G B、¥99→九十九元、2026-10-18→二零二六年十月十八日。', suggest: '开启' },
      { name: '规范化语言', desc: '目前只有中文规则完整；选英文则原样输出。', suggest: '中文 (zh)' },
    ],
    tips: [
      '规范化是幂等的，同一段文本重复下发不会被改两次。',
      '质检比对用的也是规范化后的文本，所以开与不开结果一致。',
    ],
  },
  {
    id: 'post',
    label: '后期处理链',
    icon: AudioLines,
    summary: '成品再过一遍低切 → 高架 → 压缩 → 峰值归一化；默认全关',
    settingsAnchor: 'post',
    steps: [
      '设置 → 后期处理 → 「启用后期处理」选开启（默认关闭，关闭时零开销）。',
      '按需调低切频率（默认 80Hz）、高架增益（默认 0，不动）、压缩阈值/压缩比、峰值归一化目标（默认 -1 dBFS）。',
      '回到工作台正常合成，任务详情里的 params._post 会记录实测峰值（前后对比）。',
    ],
    fields: [
      { name: '启用后期处理', desc: '总开关；关闭时不做任何处理，输出与模型原生完全一致。', suggest: '关闭' },
      { name: '低切频率 Hz', desc: '去掉低频隆隆声，常用 80；0 关闭。', suggest: '80' },
      { name: '高架增益 dB', desc: '抬升 4kHz 以上让齿音更清晰，0 表示不动。', suggest: '0' },
      { name: '压缩', desc: '超过阈值的部分按比率压，音量更稳、不容易忽大忽小。', suggest: '开启' },
      { name: '压缩阈值 dB', desc: '多大声开始压，越接近 0 压得越早。', suggest: '-18' },
      { name: '压缩比', desc: '超过阈值后压掉多少，2.5 表示超出部分缩到 1/2.5。', suggest: '2.5' },
      { name: '峰值归一化 dBFS', desc: '把最大峰值拉到指定电平，-1 是常见留余量做法；留空则不归一化（仍会硬限幅到 ±1.0 防削波）。', suggest: '-1' },
    ],
    tips: [
      '处理只作用于成品音频，不改引擎参数，所以换引擎也能用。',
      '处理链是纯 numpy 实现，不依赖 ffmpeg/scipy；长音频也是向量化计算，不会变慢。',
    ],
  },
  {
    id: 'export',
    label: '导出档位',
    icon: Zap,
    summary: 'Original 原生 / Standard 44.1k MP3 192k / For editing 48k 24bit WAV',
    settingsAnchor: 'defaults',
    steps: [
      '设置 → 默认参数 → 「导出档位」选一个默认值（默认 Original）。',
      '工作台 → 高级参数 → 「导出档位」按任务临时覆盖（选「跟随设置」就用默认值）。',
      '任务完成后到 任务中心 → 展开任务，「实际规格」一行会显示真实采样率/声道/位深/格式/体积。',
    ],
    fields: [
      { name: 'Original（模型原生）', desc: '不重采样、直接 WAV PCM_16；Qwen3-TTS 原生 24kHz。', suggest: '日常用' },
      { name: 'Standard（44.1k MP3 192k）', desc: '重采样到 44.1kHz 再转单声道 MP3 192k，体积最小，适合试听/分发。', suggest: '要发人听' },
      { name: 'For editing（48k 24bit WAV）', desc: '重采样到 48kHz、24bit WAV，进剪辑软件再处理不损失精度。', suggest: '要后期剪辑' },
      { name: '导出档位', desc: '任务级参数，留空（跟随设置）时用设置里的默认档位。', suggest: '跟随设置' },
    ],
    tips: [
      '档位会覆盖「输出格式」：Standard 固定出 MP3，For editing 固定出 24bit WAV，Original 固定 WAV。',
      '规格是读真实文件头得到的（soundfile/ffprobe），不是按配置写死的数字。',
      '要 MP3 但机器上没有 ffmpeg 时会明确报错并回退成原生 WAV，日志里写清原因，不会静默改档位。',
    ],
  },
  {
    id: 'duration',
    label: '目标时长',
    icon: Timer,
    summary: '让成品正好落在指定秒数，配音对齐用',
    settingsAnchor: 'defaults',
    steps: [
      '工作台 → 高级参数 → 「目标时长 target_duration_s」填秒数（留空表示不控制）。',
      '也可以在 设置 → 默认参数 → 「目标时长（秒）」里改默认值。',
      '合成后用保音高时间伸缩把时长拉到目标；超出 0.5-2.0 倍范围的部分用截断或补静音兜底。',
    ],
    fields: [
      { name: '目标时长 target_duration_s', desc: '优先级高于语速；生成后按目标时长做时间伸缩（不改变音高）。', suggest: '留空' },
      { name: '目标时长（秒）', desc: '设置页里的默认值，0 或留空表示不控制。', suggest: '留空' },
    ],
    tips: [
      '实测数据写回任务参数 params._post：stretch_factor / duration_before / duration_after；被夹取时会标 stretch_clamped=true。',
      '这是「事后对齐」而不是模型级时长条件，所以目标与原时长差太多（超过 2 倍）时只能截断或补静音，听感会有损失。',
    ],
  },
  {
    id: 'batch',
    label: '批量队列',
    icon: ListChecks,
    summary: '一次提交多条文本，实时看进度、可取消、可只重试失败项',
    settingsAnchor: undefined,
    steps: [
      '任务中心 → 右上角「批量提交」。',
      '文本框里一行一条（最多 50 条 / 合计 5 万字），设好整批语速，点「开始批量生成」。',
      '面板里会显示批次进度条与逐项状态（排队中/运行中/已完成/失败/已取消）。',
      '中途想停就点「取消批次」；有失败项时点「重试失败项」，会把这些项复制成一个新批次重跑，逐项参数（音色/指令/语言/参数）原样保留。',
      '全部跑完点「下载打包产出」，zip 里带 manifest.json 记录失败项与原因。',
    ],
    fields: [
      { name: '每行一条线索', desc: '按换行拆分，空行自动忽略。', suggest: '一行一句' },
      { name: '整批语速', desc: '整批统一的速度，逐项还能单独覆盖。', suggest: '1.0' },
      { name: '重试失败项', desc: '只把失败的项复制成新批次，已完成的不重跑。', suggest: '有失败时点' },
      { name: '取消批次', desc: '取消批内所有未结束任务，已完成的保留。', suggest: '—' },
      { name: '清空当前批次', desc: '只是清掉界面里记住的批次号，不删任务。', suggest: '—' },
    ],
    tips: [
      '批次号记在浏览器本地，关掉弹窗再打开还能看到同一批的进度。',
      '批次是「一批普通任务 + 一份清单」，不是父任务，所以任务中心照样能逐条重试/删除。',
    ],
  },
  {
    id: 'mcp',
    label: 'MCP Server',
    icon: Terminal,
    summary: '把合成/转写/任务查询暴露给外部 Agent 调用',
    settingsAnchor: undefined,
    steps: [
      '启动后端服务（MCP 会通过本机接口调用它）。',
      '在 backend 目录执行：python -m app.mcp_server（stdio 传输，行分隔 JSON-RPC）。',
      '在支持 MCP 的客户端里把它配成 stdio server，即可看到 9 个工具（合成 / 转写 / 提交 / 取消 / 查任务 / 音色）。',
    ],
    fields: [
      { name: 'tts_synthesize', desc: '给文本 + 音色，返回 task_id、状态与下载地址。', suggest: '—' },
      { name: 'asr_transcribe', desc: '给本机音频路径，可选 diarize/max_speakers，返回 transcript 与 segments。', suggest: '—' },
      { name: 'task_status', desc: '查任务状态、进度、错误码与结果文件。', suggest: '—' },
      { name: 'voice_list', desc: '列出内置与自定义音色（含 language / gender / available）。', suggest: '—' },
      { name: 'tts_submit / asr_submit', desc: '提交后立刻返回 task_id，不阻塞；再用 task_status 轮询。', suggest: '—' },
      { name: 'task_cancel', desc: '取消排队中或运行中的任务。', suggest: '—' },
      { name: 'voice_clone', desc: '参考音频克隆自定义音色，返回 voice_id。', suggest: '—' },
      { name: 'voice_design', desc: '自然语言描述音色并直接合成。', suggest: '—' },
    ],
    tips: [
      'MCP 进程不自己加载模型（4GB 卡会被吃满显存），而是复用本机 8443 的服务，排队/取消/日志都跟界面一致。',
      '环境变量：QWENTTS_MCP_BASE_URL（默认 https://127.0.0.1:8443）、QWENTTS_MCP_API_KEY、QWENTTS_MCP_TIMEOUT（等待任务完成，默认 600s）、QWENTTS_MCP_REQUEST_TIMEOUT（单次请求，默认 300s）。',
      '非法 JSON 行会回 -32700 Parse error，未知方法回 -32601，工具内部异常回 isError=true。',
    ],
  },
  {
    id: 'mcpConnect',
    label: '用其它 AI 调用',
    icon: Terminal,
    summary: '让 DSH / Claude Desktop / Cursor 这类 AI 客户端接上本机 MCP，直接让它帮你合成与转写',
    settingsAnchor: undefined,
    steps: [
      '先让「声匠」后端跑起来（浏览器打开 https://localhost:8443 能出界面即可）：MCP 进程只当“翻译官”，真正干活的是这个服务。',
      '找到本机 python.exe 的绝对路径，以及 backend 目录的绝对路径（下面的示例按本机情况填好，换成你自己的即可）。',
      '在 AI 客户端里新增一个 stdio 类型（命令启动）的 MCP Server，把 command / args / cwd 三项填进去，然后重启客户端。',
      '接上之后你应该能看到 9 个工具：tts_synthesize、tts_submit、asr_transcribe、asr_submit、task_status、task_cancel、voice_list、voice_clone、voice_design；直接对 AI 说“用 tts_synthesize 把这段合成语音”就会调过来。',
    ],
    snippets: [
      {
        title: '把这段直接发给你的 AI（它会自己配好接入）',
        note: '复制下面整段发给任意支持 MCP 的 AI（DSH / Claude / Cursor / Cline 都行），它会自己完成配置；里面的 python 与 backend 路径可按你机器改，不改通常也能用。',
        code: `你正在和我机器上的「声匠 VoiceSmith」语音工作台对接，请按下面的信息把它的 MCP Server
配到你自己身上，然后告诉我可以调用哪些能力。

【它是什么】
- 本机已运行的语音工作台：语音生成（TTS）、声音克隆、音色设计、音频转文字（ASR）
- 对外暴露一个 MCP Server：stdio 传输、JSON-RPC 2.0、协议版本 2024-11-05
- MCP 进程本身不加载模型，只是通过本机 HTTPS 接口调用工作台；排队、取消、进度都由工作台管

【连接参数（stdio）】
command: C:\\Users\\xiyih\\AppData\\Local\\Programs\\Python\\Python310\\python.exe
args:    ["-m", "app.mcp_server"]
cwd:     E:\\trae_projects\\QWENTTS\\backend
env:
  QWENTTS_MCP_BASE_URL = https://127.0.0.1:8443
  QWENTTS_MCP_TIMEOUT = 600
（如工作台开了 API Key，再加 QWENTTS_MCP_API_KEY=<key>；证书是自签的，MCP 内部已忽略校验）

【可用工具（共 9 个）】
1) tts_synthesize —— 文本转语音（会等完成）
   text(必填)、voice_id、language、wait(默认 true)、speed、target_duration_s
2) tts_submit —— 只提交不等待，立刻返回 task_id（长文本推荐）
3) asr_transcribe —— 音频转文字（会等完成）
   audio_path(必填，本机绝对路径)、language、diarize、max_speakers、wait
4) asr_submit —— 只提交不等待，立刻返回 task_id
5) task_status —— 查状态/进度/错误码/输出路径
6) task_cancel —— 取消排队中或运行中的任务
7) voice_list —— 列音色，字段含 id/name/kind/language/gender/age/accent/available，可按 language 过滤
8) voice_clone —— 用参考音频克隆自定义音色（audio_path + name + 可选 ref_text），返回 voice_id
9) voice_design —— 自然语言描述音色并合成（design 必填，可带 text）

【返回语义（重要）】
- 合成/转写成功：ok=true、status=completed，并给出
  task_id、duration_sec、elapsed_ms、first_packet_ms、rtf、
  output_file、output_path（本机绝对路径，优先用它读文件）、download_url
- 失败/超时/被取消：isError=true、ok=false，并带 error_code 与 error_message —— 请按失败处理，不要当成功
- output_path 形如 E:\\trae_projects\\QWENTTS\\data\\outputs\\<task_id>\\<task_id>.wav

【使用建议】
- language 可以写别名（zh / en / ja），服务端会映射成 chinese / english / japanese；写错会在建任务前就报错
- 长文本先用 tts_submit 拿 task_id，再用 task_status 轮询，避免单个调用等太久
- 需要按说话人分段时，asr_transcribe 把 diarize 打开，读 segments（时间字段是 start_s / end_s）
- 想用自定义音色：先 voice_clone 拿 voice_id，再 tts_synthesize 时指定它

【你现在要做的事】
1) 把上面的 MCP Server 配到你自己的 MCP 客户端配置里（stdio 类型），并重启/重载使配置生效
2) 连接成功后用 tools/list 确认能看到上面 9 个工具
3) 用 voice_list 取一个音色，再用 tts_synthesize 合成一句「你好，我是声匠」作为连通性验证
4) 把结果（task_id、耗时、output_path）回报给我`,
      },
      {
        title: 'DSH / 通用 MCP 客户端（mcpServers 写法）',
        note: 'DSH 及各客户端字段名可能略有差异（mcpServers / mcp.servers / servers），把 command、args、cwd、env 四项对应填进去即可。',
        code: `{
  "mcpServers": {
    "qwentts": {
      "type": "stdio",
      "command": "C:\\Users\\xiyih\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "E:\\trae_projects\\QWENTTS\\backend",
      "env": {
        "QWENTTS_MCP_BASE_URL": "https://127.0.0.1:8443",
        "QWENTTS_MCP_TIMEOUT": "600"
      }
    }
  }
}`,
      },
      {
        title: 'Claude Desktop（claude_desktop_config.json）',
        note: 'Windows 路径：%APPDATA%\\Claude\\claude_desktop_config.json；改完完全退出 Claude 再启动。',
        code: `{
  "mcpServers": {
    "qwentts": {
      "command": "C:\\Users\\xiyih\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "E:\\trae_projects\\QWENTTS\\backend"
    }
  }
}`,
      },
      {
        title: 'Cursor / Cline / Continue（MCP 设置里的 JSON）',
        note: '这三家都用同一套 mcpServers 结构；如果界面是表单式，就按 command / args / cwd 三个框分别填。',
        code: `{
  "mcpServers": {
    "qwentts": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "E:\\trae_projects\\QWENTTS\\backend"
    }
  }
}`,
      },
      {
        title: '先在命令行自测（确认能握手再配客户端）',
        note: '粘进去按回车，应立刻返回一行带 "serverInfo" 的 JSON；再用 tools/list 看 9 个工具名。报错就先解决这一步。',
        code: `cd E:\\trae_projects\\QWENTTS\\backend
echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}} | python -m app.mcp_server`,
      },
      {
        title: '让它干活的两个典型请求（对 AI 说，或直接 tools/call）',
        note: 'audio_path 必须是本机绝对路径；合成会返回任务 id 与下载地址，长文本会自动排队。',
        code: `tts_synthesize  { "text": "欢迎使用声匠。", "voice_id": "builtin-vivian", "speed": 1.0 }
asr_transcribe  { "audio_path": "E:\\trae_projects\\QWENTTS\\data\\demo.wav", "diarize": true, "max_speakers": 3 }`,
      },
    ],
    tips: [
      '自检命令：cd backend 后跑 python -m app.mcp_server --selftest（initialize → tools/list → health → voice_list → 真实合成 → 转写回环，6 步应全 PASS）。',
      'MCP 进程**不自己加载模型**（4GB 卡会被吃满显存），它只是通过本机 https://127.0.0.1:8443 调接口；所以「声匠」必须开着。',
      '服务不在默认地址时用环境变量改：QWENTTS_MCP_BASE_URL（默认 https://127.0.0.1:8443）；开了 API Key 鉴权再设 QWENTTS_MCP_API_KEY。',
      '长任务等待时间由 QWENTTS_MCP_TIMEOUT 控制（默认 600s）；单次 HTTP 请求超时是 QWENTTS_MCP_REQUEST_TIMEOUT（默认 300s）。',
      '客户端里看到“找不到 python”就换成 python.exe 的绝对路径；看到 401/403 就是服务端开了鉴权但没配 QWENTTS_MCP_API_KEY。',
      '自签证书不需要额外配置：MCP 内部已忽略证书校验，只连本机。',
    ],
    faq: [
      {
        q: '为什么不在 MCP 里直接加载模型？',
        a: '同一张显卡上再起一套推理进程会直接把显存吃满；走本机 HTTP 还能复用界面的排队、取消、日志与进度。',
      },
      {
        q: 'AI 说工具调用超时怎么办？',
        a: '先看任务中心里这条任务是否还在跑：还在跑说明模型慢，把 QWENTTS_MCP_TIMEOUT 调大；如果任务已失败，按错误码查「常见错误」。',
      },
    ],
  },
  {
    id: 'defaults',
    label: '默认参数',
    icon: ServerCog,
    summary: '新建任务时的初始值；工作台里可以按任务临时改',
    settingsAnchor: 'defaults',
    fields: [
      { name: '语速', desc: '0.5–2.0，1.0 为原速。', suggest: '1.0' },
      { name: '音高（半音）', desc: '-12 到 +12，单位是半音。', suggest: '0' },
      { name: '温度', desc: '采样温度，越低越稳、越高越有变化。', suggest: '0.9' },
      { name: 'Top P', desc: '核采样阈值，越小越保守。', suggest: '0.8' },
      { name: '采样步数', desc: '256–4096，越大越细越慢。', suggest: '2048' },
      { name: '输出格式', desc: 'wav（无损）或 mp3（体积小）。', suggest: 'wav' },
    ],
    tips: [
      '这些值只是「新建任务时」的初值，改完不会回头修改已有任务或已生成的音频。',
      '采样步数对耗时影响最直接；机器慢就往下调，不要动温度去凑。',
    ],
  },
  {
    id: 'license',
    label: '授权与数据',
    icon: BadgeCheck,
    summary: '模型许可、数据位置与密钥隐私（只读说明）',
    settingsAnchor: 'license',
    fields: [
      { name: '模型许可', desc: '本地模型权重由各自发布方授权，商用条款以模型仓库页面为准，每个引擎的具体许可证写在该引擎卡片里。', suggest: '商用前逐个确认' },
      { name: '数据位置', desc: '自定义音色、参考音频与生成结果只保存在本机 data/ 目录，不会自动上传；只有云端/混合模式合成时才按需上传参考音频。', suggest: '—' },
      { name: '密钥与隐私', desc: 'API 密钥、SSH 密码写入 data/runtime.json，界面始终脱敏显示，请自行控制该目录访问权限。', suggest: '—' },
    ],
    tips: [
      '把 data/ 目录改成共享目录或提交到 Git 之前，先确认里面没有 runtime.json。',
      '软件本身为闭源授权；第三方组件与模型许可见 THIRD_PARTY_NOTICES.md。',
    ],
  },
  {
    id: 'errors',
    label: '常见错误速查',
    icon: LifeBuoy,
    summary: '界面上的错误码是什么意思、先做什么',
    fields: [
      { name: 'VRAM_INSUFFICIENT', desc: '本地显存不足（含各种 OOM 写法）。四选一：换 0.6B 引擎 / 切云端 / 推理设备改 cpu / 缩短单句。', suggest: '先看「本地模型」分区' },
      { name: 'MODEL_MISSING', desc: '模型文件不存在，需要先安装或下载。', suggest: '「引擎与模型」点安装' },
      { name: 'MODEL_LOAD_FAILED', desc: '加载失败，常见原因：显存不足、文件不完整、CUDA 环境异常。', suggest: '看「状态与日志」原始日志' },
      { name: 'CLOUD_UNREACHABLE', desc: '连不上云端服务：地址/端口/隧道/实例已关机。', suggest: '「云端连接」点测试连接' },
      { name: 'CLOUD_AUTH_FAILED', desc: '云端鉴权失败，密钥不对。', suggest: '改 API 密钥' },
      { name: 'CLOUD_NOT_READY', desc: '云端正在加载模型，会自动重试并在就绪后继续。', suggest: '等，别重复提交' },
      { name: 'PACKAGE_MISSING / VENV_FAILED / VERSION_MISMATCH', desc: '引擎依赖问题：装在每个引擎自己的 venv 里，切勿在主环境装。', suggest: '复制卡片上的安全安装命令' },
      { name: 'ENGINE_CAPABILITY_MISSING', desc: '当前引擎不支持该能力（例如克隆/音色设计只有 Qwen3-TTS 1.7B 有）。', suggest: '切引擎或改用普通合成' },
      { name: 'INTERRUPTED', desc: '服务重启导致任务中断，已生成的片段保留。', suggest: '任务中心点重试' },
    ],
    tips: [
      '「状态与日志」页有完整的错误码、中文处理建议与最近日志，排障先看那一页。',
      '提交问题时把错误码 + 原始报错文本一起给出，能直接判断是显存、驱动还是架构不支持。',
    ],
  },
];

const SECTION_IDS = GUIDE.map((s) => s.id);

const ANCHOR_PREFIX = 'guide-';

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function scrollToSection(id: SectionId) {
  const node = document.getElementById(`${ANCHOR_PREFIX}${id}`);
  if (!node) return;
  node.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
}

/** 分区里所有可搜索文本（搜索索引，只在内容变化时算一次） */
function haystack(section: GuideSection): string {
  const parts: string[] = [section.label, section.summary];
  if (section.steps) parts.push(...section.steps);
  if (section.tips) parts.push(...section.tips);
  section.snippets?.forEach((sn) => parts.push(sn.title, sn.note || '', sn.code));
  section.fields?.forEach((f) => parts.push(f.name, f.desc, f.suggest || ''));
  section.faq?.forEach((f) => parts.push(f.q, f.a));
  return parts.join(' ').toLowerCase();
}

const INDEX = new Map<SectionId, string>(GUIDE.map((s) => [s.id, haystack(s)]));

export function GuidePage() {
  const [search, setSearch] = useState('');
  const [activeSection, setActiveSection] = useState<SectionId>('start');
  const query = search.trim().toLowerCase();

  const matchedSections = useMemo(() => {
    if (!query) return null;
    const ids = new Set<SectionId>();
    INDEX.forEach((text, id) => {
      if (text.includes(query)) ids.add(id);
    });
    return ids;
  }, [query]);

  const isShown = useCallback(
    (id: SectionId) => !matchedSections || matchedSections.has(id),
    [matchedSections],
  );

  /** 只高亮分区标题与字段名：正文里搜索「1」这类单字符不会满屏高亮 */
  const hl = useCallback(
    (text: string): React.ReactNode => {
      if (!query) return text;
      const index = text.toLowerCase().indexOf(query);
      if (index < 0) return text;
      return (
        <>
          {text.slice(0, index)}
          <mark className="hl">{text.slice(index, index + query.length)}</mark>
          {text.slice(index + query.length)}
        </>
      );
    },
    [query],
  );

  const navItems = useMemo(
    () => GUIDE.filter((section) => !matchedSections || matchedSections.has(section.id)),
    [matchedSections],
  );

  const jumpedRef = useRef(false);

  /* 滚动联动高亮：IntersectionObserver + rAF 节流的几何校正（与设置页同款） */
  useEffect(() => {
    const nodes = SECTION_IDS.map((id) => document.getElementById(`${ANCHOR_PREFIX}${id}`)).filter(
      Boolean,
    ) as HTMLElement[];
    if (!nodes.length) return;

    const compute = () => {
      const offset = 96;
      let current: SectionId = GUIDE[0].id;
      let lastVisible: SectionId | null = null;
      GUIDE.forEach((section) => {
        const el = document.getElementById(`${ANCHOR_PREFIX}${section.id}`);
        if (!el || el.offsetParent === null) return; // 被搜索过滤掉的不参与
        lastVisible = section.id;
        if (el.getBoundingClientRect().top - offset <= 0) current = section.id;
      });
      const scroller = document.scrollingElement;
      const atBottom = scroller ? window.innerHeight + window.scrollY >= scroller.scrollHeight - 8 : false;
      if (atBottom && lastVisible) current = lastVisible;
      setActiveSection(current);
    };

    const observer = new IntersectionObserver(compute, { rootMargin: '-96px 0px -58% 0px', threshold: 0 });
    nodes.forEach((node) => observer.observe(node));

    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        compute();
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    compute();
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [matchedSections]);

  /* 支持直接分享 #/guide?s=local 落到某个分区（只跳一次） */
  useEffect(() => {
    if (jumpedRef.current) return;
    const raw = window.location.hash.replace(/^#\/?/, '');
    const index = raw.indexOf('?');
    if (index < 0) return;
    const id = new URLSearchParams(raw.slice(index + 1)).get('s');
    if (!id || !SECTION_IDS.includes(id as SectionId)) return;
    jumpedRef.current = true;
    window.setTimeout(() => {
      setActiveSection(id as SectionId);
      scrollToSection(id as SectionId);
    }, 80);
  }, []);

  const goSettings = (anchor?: string) => {
    window.location.hash = anchor ? `#/settings?s=${anchor}` : '#/settings';
  };

  return (
    <div className="page guide-page">
      <div className="page__head">
        <div className="toolbar">
          <button type="button" className="btn btn--sm" onClick={() => scrollToSection(GUIDE[0].id)}>
            <ArrowUp size={14} strokeWidth={1.75} /> 回到顶部
          </button>
        </div>
      </div>

      <div className="settings-body">
        <nav className="settings-nav" aria-label="教程分区">
          <div className="settings-nav__search">
            <Search size={14} strokeWidth={1.75} aria-hidden="true" />
            <input
              className="input"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索功能，如「显存」「隧道」"
              aria-label="搜索教程内容"
            />
            {search ? (
              <button
                type="button"
                className="settings-nav__clear"
                onClick={() => setSearch('')}
                aria-label="清除搜索"
                title="清除搜索"
              >
                <X size={13} strokeWidth={2} />
              </button>
            ) : null}
          </div>
          <p className="settings-nav__status" aria-live="polite">
            {query ? (
              <>
                命中 <span className="num">{navItems.length}</span> / {GUIDE.length} 个分区
              </>
            ) : (
              <>
                共 <span className="num">{GUIDE.length}</span> 个分区
              </>
            )}
          </p>
          <ul className="settings-nav__list">
            {navItems.map((section) => {
              const Icon = section.icon;
              return (
                <li key={section.id}>
                  <button
                    type="button"
                    className="settings-nav__item"
                    aria-current={activeSection === section.id}
                    onClick={() => {
                      setActiveSection(section.id);
                      scrollToSection(section.id);
                    }}
                  >
                    <Icon size={14} strokeWidth={1.75} aria-hidden="true" />
                    <span>{hl(section.label)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <select
            className="select settings-nav__select"
            value={activeSection}
            aria-label="跳转到教程分区"
            onChange={(event) => {
              const id = event.target.value as SectionId;
              setActiveSection(id);
              scrollToSection(id);
            }}
          >
            {navItems.map((section) => (
              <option key={section.id} value={section.id}>
                {section.label}
              </option>
            ))}
          </select>
        </nav>

        <div className="settings-content">
          <InlineNote kind="info">
            这一页是纯说明，不会修改任何配置，也不会加载模型。要改设置请点各分区的「跳到设置」，改完约
            0.7 秒自动保存。
          </InlineNote>

          {matchedSections && navItems.length === 0 ? (
            <div className="settings-empty" role="status">
              <p>没有匹配「{search.trim()}」的教程内容。</p>
              <div className="toolbar">
                <button type="button" className="btn btn--sm" onClick={() => setSearch('')}>
                  清除搜索
                </button>
                <span className="hint">试试「显存」「隧道」「质检」「0.6B」这类关键词。</span>
              </div>
            </div>
          ) : null}

          {GUIDE.map((section) => {
            const Icon = section.icon;
            return (
              <SectionCard
                key={section.id}
                id={`${ANCHOR_PREFIX}${section.id}`}
                title={hl(section.label)}
                hint={section.summary}
                icon={<Icon size={15} strokeWidth={1.75} />}
                style={isShown(section.id) ? undefined : { display: 'none' }}
                actions={
                  section.settingsAnchor ? (
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => goSettings(section.settingsAnchor)}
                      title="打开设置页的对应分区"
                    >
                      <ArrowUpRight size={13} strokeWidth={1.75} /> 跳到设置
                    </button>
                  ) : null
                }
              >
                {section.steps?.length ? (
                  <ol className="guide-steps">
                    {section.steps.map((step, index) => (
                      <li key={index}>{step}</li>
                    ))}
                  </ol>
                ) : null}

                {section.snippets?.length ? (
                  <div className="guide-snippets">
                    {section.snippets.map((sn, index) => (
                      <div className="guide-snippet" key={index}>
                        <div className="guide-snippet__head">
                          <span className="guide-snippet__title">{sn.title}</span>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() => {
                              void navigator.clipboard?.writeText(sn.code);
                            }}
                            title="复制这段配置"
                          >
                            复制
                          </button>
                        </div>
                        {sn.note ? <p className="guide-snippet__note">{sn.note}</p> : null}
                        <pre className="guide-code">
                          <code>{sn.code}</code>
                        </pre>
                      </div>
                    ))}
                  </div>
                ) : null}

                {section.fields?.length ? (
                  <dl className="guide-fields">
                    {section.fields.map((field) => (
                      <div className="guide-fields__row" key={field.name}>
                        <dt>
                          {/^[A-Z][A-Z0-9_ /]*$/.test(field.name) ? <code>{field.name}</code> : hl(field.name)}
                        </dt>
                        <dd>
                          <span>{field.desc}</span>
                          {field.suggest ? (
                            <span className="guide-fields__suggest">
                              建议：<code>{field.suggest}</code>
                            </span>
                          ) : null}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : null}

                {section.faq?.length ? (
                  <div className="guide-faq">
                    {section.faq.map((item) => (
                      <details key={item.q}>
                        <summary>{item.q}</summary>
                        <p>{item.a}</p>
                      </details>
                    ))}
                  </div>
                ) : null}

                {section.tips?.length ? (
                  <InlineNote kind="warning">
                    <ul className="guide-tips">
                      {section.tips.map((tip, index) => (
                        <li key={index}>{tip}</li>
                      ))}
                    </ul>
                  </InlineNote>
                ) : null}
              </SectionCard>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default GuidePage;
