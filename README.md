# Steam 统译 (Steam Unify)

[![Tests](https://github.com/icefirercom/steam-unify/actions/workflows/tests.yml/badge.svg)](https://github.com/icefirercom/steam-unify/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Upstream](https://img.shields.io/badge/Upstream-moutou232%2Fsteam--unify-8b5cf6.svg)](https://github.com/moutou232/steam-unify)

把 Steam 库里的游戏名补成**简体中文**，并按类型/标签自动分类（每个游戏最多 3 个分类），写入 Steam 收藏集；重启 Steam 后仍然保留。

本项目基于开源上游项目 [**moutou232/steam-unify**](https://github.com/moutou232/steam-unify)（MIT 协议）进行深度优化与功能升级，保留纯本地运行、零第三方 Python 依赖与可逆备份机制，重点提升**游戏库发现完整度、中文译名命中率与质量、翻译优先级自定义、v29 缓存写入稳定性**以及**桌面界面交互体验**。

> **Steam Unify** is a zero-dependency local Windows utility that unifies Steam library titles into Simplified Chinese and organizes games into multi-label Steam collections. Based on [moutou232/steam-unify](https://github.com/moutou232/steam-unify) with enhanced library discovery, system-tool filtering, configurable multi-source translation priority, critical `appinfo.vdf` v29 sentinel fix, and a redesigned Tkinter GUI.

---

## 上游项目与致谢

- **上游仓库**：[https://github.com/moutou232/steam-unify](https://github.com/moutou232/steam-unify)
- **开源协议**：[MIT License](LICENSE)（完整保留上游提交历史与版权声明）
- **本仓库**：[https://github.com/icefirercom/steam-unify](https://github.com/icefirercom/steam-unify)（v2.0.0 增强定版）

---

## v2.0 核心功能与改进

### 1. 完整游戏库扫描 & 智能过滤系统工具
- **不再漏掉“未曾启动的已购游戏”**：新增纯 Python 解析 Steam `appcache/packageinfo.vdf`（支持 v39 / v40 格式）与 `userdata/<account>/config/licensecache`（protobuf），自动识别已购买但从未安装/未生成 `localconfig.vdf` 记录的库内游戏。
- **公共 Sub 0 排除与多账号隔离**：自动排除 Steam 公共免费/工具包（`package_id == 0`），避免把系统运行库或全区公共条目误判为库内游戏；同一台电脑登录多个 Steam 账号时，优先依据当前账号的 `licensecache` 进行授权隔离。
- **自动过滤系统工具与非游戏项目**：内置核心运行库/开发工具黑名单（Steamworks Common Redistributables、Spacewar、SteamVR、Proton / Steam Linux Runtime、Source SDK、AMD Driver Updater 等）及正则启发式规则，自动归入「已过滤」视图；支持手动加入/移出「已忽略」名单。

### 2. 高命中率多源并发汉化 & 自定义优先级排序
- **全来源统一可排序**：支持以下 9 个翻译来源的顺序自定义（上移 / 下移 / 置顶 / 恢复默认）以及单个来源启用或停用：
  1. **用户词典**（`user_dict`：`data/user_names.json`，手动修改与导入同步）
  2. **Steam 内置**（`official`：`appinfo` 原生简体中文 + **繁体自动转简体**）
  3. **内置词典**（`bundled_dict`：`data/official_names.json`）
  4. **维基百科**（`wikidata`：Wikidata SPARQL 按 Steam AppID `P1733` 批量查询标准中文译名，每批 80 款）
  5. **Steam 商店**（`steam_store`：并发查询简中 / 繁转简名称，并顺带缓存商店简介供分类打分）
  6. **AI 大模型**（`llm`：可选 OpenAI 兼容接口，支持 DeepSeek、硅基流动、OpenAI、本地 Ollama 等批量翻译）
  7. **Google 机翻**（`google`：并发免费机翻，支持自动识别日/韩/英等多语种）
  8. **有道机翻**（`youdao`：并发免费机翻，国内直连可用）
  9. **MyMemory**（`mymemory`：兜底免费机翻）
- **质量与网络修复**：
  - 修复系统代理被默认绕过的问题，支持默认使用系统代理或手动指定 HTTP/SOCKS 代理；
  - 严格校验中文字符（拒绝英文回退污染及纯日文假名标题伪装成中文），并自动修复早期误标为 `steam` 的英文残留；
  - 保留标准版本后缀（如 *Game of the Year Edition* → `年度版`、*Definitive Edition* → `决定版` 等）；
  - 各在线机翻引擎内置并发池与连续失败熔断器，避免无网络或限流时长时间卡死。

### 3. 自定义词典与多标签自动分类
- **分层自定义词典**：支持在主界面或设置中导入/导出 JSON 与 CSV 词典，手动编辑译名时可同步写入个人词典并支持名称锁定（🔒）。
- **多标签分类 + Steam 收藏集**：结合 Steam 类型、商店标签、简介关键词与本地分类权重模型，为每款游戏自动推荐最多 3 个分类；支持批量追加/替换/移除、撤销上一次批量修改以及主分类调整。

### 4. Steam v29 `appinfo.vdf` 关键修复与容错写入
- **修复重启 Steam 报错 / 缓存被清空问题**：补齐 Steam v29 `appinfo.vdf` 条目区与字符串表之间必须存在的 4 字节零值哨兵（`0x00000000`），序列化后未修改数据可与 Steam 原文件实现 **100% 字节级一致**，写入前内置完整性与哨兵自检。
- **独立应用选项**：应用写入弹窗支持独立勾选「写入中文游戏名」与「写入 Steam 收藏集」，按全部或勾选范围应用。
- **启动高容错**：即使本机因历史缓存损坏导致 `appinfo.vdf` 暂时缺失，程序仍可正常启动，并支持直接点击「更多操作 → 从备份还原」恢复最近备份。

### 5. 全新高效桌面界面
- **流畅大库体验**：采用 Canvas 虚拟滚动列表与零重绘悬停优化，支持高 DPI 自适应与居中显示；大库滚动、筛选、搜索秒级响应。
- **清晰的工作流布局**：精简为单行主工作流工具栏 + 「更多操作 ▾」二级菜单 + 左侧边栏联动筛选 + 「需核对 / 已忽略 / 已过滤」专项视图。
- **快捷键与实时反馈**：
  - `Ctrl+F`：聚焦搜索框（内置输入防抖）
  - `Enter` / `F2`：打开译名编辑弹窗（可异步并发查询各来源候选译名并一键对比选用）
  - `Space`：勾选/取消当前行，`Ctrl+A` 全选当前列表，`Esc` 取消选择
  - 自动翻译时支持列表**流式实时刷新**，无需等待整批结束。

---

## 快速开始

### 环境要求
- Windows 10 / 11
- Python 3.10+（推荐 Python 3.12 / 3.13，**零第三方运行依赖**，仅使用标准库与 Tkinter）

### 从源码运行

```powershell
git clone https://github.com/icefirercom/steam-unify.git
cd steam-unify
python run.py
```

也可以直接双击项目根目录的 `启动.bat`（或使用 `pythonw run.pyw` 无控制台窗口启动）。

### 命令行模式（CLI）

```powershell
# 仅扫描游戏库并输出列表与统计信息（不写入 Steam）
python run.py --scan

# 静默修复已被 Steam 更新覆盖的中文名与收藏集
python run.py --autofix

# 导出当前库清单与译名/分类报告到 CSV
python run.py --report library_report.csv
```

### 构建独立便携版 EXE（可选）

如果需要打包给未安装 Python 的电脑使用（需要安装 PyInstaller 打包工具）：

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

> `build_exe.ps1` 已配置为**仅打包静态词典与分类模型**，不会打包本机的 `settings.json`（含 API Key）、`state.json` 或个人备份。

---

## 翻译源优先级与 AI 翻译配置

1. 打开程序右上角 **「设置」**：
   - **「Steam 与过滤」**：指定/自动检测 Steam 安装目录与当前账号；开启或关闭「库发现增强」「非游戏/工具过滤」「收藏集同步」「保留备份数」等。
   - **「翻译与网络」**：
     - 在**翻译源优先级列表**中使用 `↑ 上移`、`↓ 下移`、`置顶`、`启用/停用`、`恢复默认` 自由定制译名命中顺序；
     - 配置是否使用系统代理、自定义代理地址、并发数，以及词典导入/导出。
   - **「AI 翻译（可选）」**：
     - 勾选启用 AI 批量翻译，内置 **DeepSeek**、**硅基流动 (SiliconFlow)**、**OpenAI**、**本地 Ollama** 等一键预设；
     - 填入 API Key 与模型名后，可点击 **「测试连接」** 实时验证模型可用性。AI 开关与优先级列表中的 `AI 大模型` 条目双向联动。

---

## 安全与隐私说明

- **纯本地离线工具**：仅读写本机 Steam 本地缓存（`appinfo.vdf` / 分片缓存 与 `cloud-storage-namespace-1.json`），绝不读取、保存或上传 Steam 账号密码、令牌或 Cookie。
- **凭据不入库**：个人设置（包括 AI API Key）、库状态、用户词典及 `backups/` 均保存在本机 `data/` 与 `backups/` 目录，已通过 `.gitignore` 严格排除，不会同步到代码仓库。
- **自动备份与回滚**：每次写入 Steam 前自动完整备份相关文件；校验失败自动回滚；支持「从备份还原」以及「一键复原（恢复原英文名并清理程序创建的收藏集）」。
- **账号作用域提示**：`appinfo` 游戏名修改对本机 Steam 安装目录共享；收藏集按当前选中账号写入 `userdata/<account>/config/cloudstorage/cloud-storage-namespace-1.json`。

---

## 工作原理

- **库发现**：结合 Steam 本地 ACF 已安装清单、`appinfo.vdf`（v29）/ `appinfo_XX.vdf`（v30 分片）、`userdata/<account>/config/localconfig.vdf`、收藏集记录以及 `packageinfo.vdf`（v39/v40）+ `licensecache`，构建完整且去重去噪的拥有游戏列表。
- **本地缓存读写**：
  - Steam v29：`appcache/appinfo.vdf`，保留并校验条目结束哨兵 `0x00000000`，同步条目 `size`、`vdf_sha1` 及 `string_table_off`。
  - Steam v30 分片：`appcache/appinfo/appinfo_XX.vdf` 与索引文件同步偏移与 CRC32 校验。
- **状态存储**：译名、分类标签、来源标记与原始信息（`orig_name` / `orig_localized`）持久化保存在本地 `data/state.json`，确保任何时候均可完整复原。

---

## 项目结构

```text
steam-unify/
├── run.py                  # GUI 与 CLI 主入口 (--scan / --autofix / --report)
├── run.pyw                 # Windows 无控制台启动入口
├── 启动.bat                # 一键启动批处理
├── build_exe.ps1           # PyInstaller 便携版构建脚本
├── LICENSE                 # MIT 开源许可证
├── CHANGELOG.md            # 版本更新日志
├── steam_zh/
│   ├── __init__.py         # 版本号声明
│   ├── paths.py            # 源码与打包环境统一路径管理
│   ├── steamenv.py         # Steam 安装目录、多账号与收藏 ID 探测
│   ├── packageinfo.py      # packageinfo.vdf (v39/v40) 与 licensecache 解析
│   ├── appinfo.py          # Steam v29 appinfo.vdf 读写与哨兵校验
│   ├── appinfo_shards.py   # Steam v30 分片缓存读写与 CRC32 校验
│   ├── vdftext.py          # 文本 VDF 解析器
│   ├── metadata.py         # 游戏元数据聚合与状态同步
│   ├── filters.py          # 系统工具/非游戏黑名单与启发式过滤
│   ├── zhconv.py           # 零依赖游戏专有繁转简引擎
│   ├── dictionary.py       # 分层用户/内置词典管理 (JSON/CSV 导入导出)
│   ├── translate.py        # 多源并发翻译引擎与优先级调度
│   ├── descriptions.py     # 商店简介并发抓取与本地缓存
│   ├── category_model.py   # 多标签分类权重模型
│   ├── scoring.py          # 综合规则/词典/标签/简介的分类评分器
│   ├── categories.py       # 分类定义与工具函数
│   ├── category_state.py   # 分类状态管理与 3 标签上限控制
│   ├── category_batch.py   # 批量追加/替换/移除与撤销支持
│   ├── collections.py      # Steam 收藏集云存储 JSON 读写
│   ├── apply.py            # 关闭 Steam、备份、原子写入、校验与回滚
│   ├── undo.py             # 一键复原（恢复原名并移除工具收藏集）
│   ├── state.py            # 持久化状态与设置默认配置
│   ├── service.py          # 业务服务层与 CLI/GUI 统一接口
│   ├── ui_widgets.py       # Canvas 虚拟滚动表格与自绘控件
│   ├── gui_dialogs.py      # 译名编辑、多源候选、应用选项与设置弹窗
│   └── gui.py              # 高 DPI 适配主界面与工作流控制
├── data/                   # 内置静态词典与分类模型（运行状态已由 .gitignore 忽略）
└── tests/                  # 单元测试套件
```

---

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

测试套件完整覆盖：v29/v30 缓存解析与哨兵字节级一致性校验、`packageinfo.vdf` / `licensecache` 授权解析、繁转简、系统工具过滤、词典导入导出、多源优先级与熔断、多标签评分、批量应用选项及 Tkinter GUI 冒烟测试。

---

## 免责声明

本项目与 Valve Corporation / Steam 无任何关联。修改 Steam 本地缓存文件存在一定风险，虽然工具内置了自动备份、写入校验、失败回滚与一键复原机制，仍建议在使用前妥善备份个人库数据，并在了解风险的前提下自行决定是否使用。
