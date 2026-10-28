# v1.1.4 — 发布包终于有了 CLI；审计链两处真缺陷；崩溃可观测性

## 最严重的问题：**发布包里的 exe 完全没有命令行**

`audiocheck-1.1.3-win64.zip` 里只有 `audiocheck.exe` 一个可执行文件，而它的入口
被硬接在 `ui.app.main` 上 ⇒ **任何参数都会开窗口进主循环**。实测
`audiocheck.exe --help` **卡死 180 秒**（超时才被杀）。

README 宣传的 `--json`、`--csv`、`--doctor`、`--list-analyzers`、批量体检——
对**压缩包用户全部不可达**（`pip` 安装路径不在他们手里）。

修复：入口按 argv 分派。无参数开界面，带参数走 CLI（GUI 的
`--seed/--mode/--vibe` 除外）。实测：

| 命令 | v1.1.3 | v1.1.4 |
|---|---|---|
| `--version` | 卡死 180 s | **1.69 s** |
| `--doctor` | 卡死 | **1.55 s** |
| 完整分析一个 WAV | 卡死 | **1.80 s**（73 字段） |
| 双击（无参数） | 正常开窗 | 正常开窗 |

顺带修了两个**只在用户机器上才暴露**的入口元数据缺陷：

* `audiocheck-edit` 同时注册在 `[project.scripts]` 与 `[project.gui-scripts]`——
  pip 落盘顺序不确定，二者丢一。CLI 版改名 `audiocheck-edit-cli`。
* `[project.gui-scripts] audiocheck-edit = "audiocheck.ui.editor:main"` 指向的
  `main` **根本不存在**（该包只导出 `EditorWindow`/`TransportBar`）⇒ pip 装完
  敲命令立刻 `AttributeError`。已补上实现。

## 审计链的两处真实缺陷（变异测试抓出来的）

这两条都是"跨机核对同一份修复"这个核心承诺的漏洞：

1. **内容哈希把 RIFF 总大小算进去了**。`h.update(blob[:12])` 里的第 4–8 字节是
   RIFF 总大小，它随容器里**任何** chunk 的长度变化 ⇒ 同一份音频只因多带一个
   `LIST`/`INFO`/`fact` 就被判成"不同内容" ⇒ **核对假失败**。
2. **`RIFX`（大端 RIFF）的 chunk size 按小端读**。size 变成天文数字，
   `min(pos + 8 + size, len(blob))` 把整个文件当一个大 chunk ⇒
   **哈希失去区分力**（不同内容可能撞成同一个值）⇒ **核对假通过**，比假失败更危险。

两条都补了判据（`tests/test_edit_render_gaps.py`，18 条），其中"钉常量"那条
用**独立复算**杀掉了 `pos+4`→`pos+5`、`pos+8`→`pos+9`、`+ (size&1)`→`-`、
`12→13` 等一整套常量变异。

## 新增：崩溃可观测性（此前完全没有）

用户说"程序崩了/卡住了"，开发者手里只有这一句话。现在：

* 轮转日志（2 MB × 3）+ `faulthandler` + 三个异常钩子
  （`sys.excepthook` / `threading.excepthook` / `sys.unraisablehook`）；
* **"导出诊断包"**：日志 + 环境信息 + `manifest.sha256` 打成 zip，
  **本地生成、不上传、不含音频、绝对路径按 `~` 缩写**；
* 三个坑都处理了：`--windowed` 下 `sys.stderr is None`、
  faulthandler 文件必须保持打开、`sys.excepthook` 不覆盖线程。

## 新增：高对比度跟随系统

Qt 的 Windows 插件在系统开高对比度时把 `colorScheme()` 返回
**`Qt::ColorScheme::Unknown`**，并改用 `GetSysColor` 重算调色板。本项目的主题是
完全硬编码的调色板 ⇒ "Unknown 当作默认深色" 的实际后果是**低视力用户的无障碍
功能失效**（Microsoft 指南明确要求 "Do not hard-code colors in HighContrast"）。

现在 `active_theme()` 检测到高对比度就用系统 `QPalette` 构造主题。

## 新增：导出不再卡住界面

编辑工作站的导出此前**同步跑在 UI 线程**：10 分钟素材整轨渲染实测约 24 秒，
这期间窗口完全不响应。现在走后台作业执行器（可取消、进度节流、异常不吞）。

## 无障碍：5 个控件此前没有可访问名称

新增判据当场抓出：文件列表、操作栈、三个下拉框既无 `accessibleName`
也无 text/toolTip ⇒ 屏幕阅读器只能念"按钮、按钮、按钮"。已全部补上。

## 打包体积 296 → 255 MB

砍掉源码零引用的 `pandas`/`lxml`/`cryptography`。

⚠ 过程中**踩了三次同一个坑**：`numpy.f2py`、`numpy.distutils`、`numpy.testing`、
`PIL` 都会在**模块加载期**被 scipy/matplotlib 访问，砍掉就 EXE 启动即崩
（构建永远成功，只有运行才炸）。已写成机器判据：
`test_spec_does_not_exclude_load_bearing_modules` —— numpy 任何子模块、
PIL/Pillow 一旦出现在 excludes 就红。

并新增 `packaging/smoke_packaged.py`：三层冒烟（CLI 通路 / 真实分析 / GUI 窗口），
正是它抓出了这三次错误排除。

## 测试与量具

* 变异工具加 `--jobs N` 并行：206 个变异体从 3 小时降到约 25 分钟。
* 修复**前缀缓存的并发竞态**（文件名内容寻址 + 目录隔离 + 引用计数）。
  此前 8 路并发跑同一个变异体得到 `killed×7 / survived×1` 的不稳定结果——
  量具被自己的缓存竞态污染了。修复后串并行结果完全一致。
* `render.py` 满上限：0.909 → **0.975**；`session.py`：**1.000**。
* 文档数字对账器修正一处**自欺**：某条注册复算口径错用素材、容差放水到 ±30
  （等于没对账）。现容差 ±0.5，声称值按台阶臂后的实测更新。
* 全量 **1468 项测试通过**。

## 算法侧的一次"不采纳"

外部调研建议把台阶咔哒检测的窗长从 21 提到 41（"L=41 是甜点"）。
`packaging/step_L_sweep.py` 在本仓语料上实测**结论相反**：L=41/81 在宽度 3/5
上**零检出**（MAD 尺子被稀释 17–27%）。`STEP_L=21` 保持不动，并加了守门判据
防止将来按调研报告误改。

---

安装：`pip install audiocheck`，或下载 `audiocheck-1.1.4-win64.zip` 解压即用
（无需 Python）。压缩包里的 `audiocheck.exe` **现在既是界面也是命令行工具**：
双击开窗口，带参数走 CLI。

SHA256: `8cb56237e21be22fc91b5b421562c2a07ed72ccbb115ab6735ed767119acacb6`
