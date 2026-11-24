# 变更记录

按日期倒序。每个条目都能在 `docs/` 里找到对应的复现步骤或验证数据。

## 2026-11-24 — 新增 API 版答题提示词：去掉哨兵与定时任务

原来的 [`docs/answer-pipeline-prompt.md`](docs/answer-pipeline-prompt.md) 假设答题者**常驻本机、盯着 `input/` 等新素材**，所以整套提示词里塞了大量「等」的机制：`watch` + `wait` 双哨兵、每小时 automation 兜底、`status` 反复查队列、处理完再重挂。这些不是答题本身需要的，而是为了补上「谁来叫醒它」这个缺口，代价是两条长期进程加一个定时任务。

换成通过 **API 密钥调用**的 Agent 后，它同样有工作区读写权、同样能跑 `pipeline.py`，差别只在**不常驻**：调用方发一次请求，它把手上这批做完就结束，不会被叫醒。于是「等素材」这一环根本不存在——哨兵与定时任务不是做不到，而是没有对象可等，配了只会白占时间。

新增 [`docs/answer-pipeline-prompt-api.md`](docs/answer-pipeline-prompt-api.md)，与 Agent 版的差别只有触发与收尾：

| 项目 | Agent 版 | API 版 |
| --- | --- | --- |
| 触发 | 哨兵监测 `input/`，定时任务兜底 | 调用方发一次请求 |
| 哨兵 / 定时任务 | 需要 | **不需要**，没有等待对象 |
| 收尾 | 队列空了回去挂哨兵，继续等 | 队列空了直接结束本次调用 |
| 读图、写稿、渲染、校验、发布 | 相同 | 相同（都是它自己做） |
| 篇幅控制 | `render --check` 实测块高 | 同左 |

同时把压缩超页的经验固化进提示词：先合并相邻短段落（省段间距、不删内容），再把正文 `\dfrac` 改 `\frac`（行内 `\dfrac` 撑高行距，一页少排三四行），最后才收句子；明令不许删「补充理解」与「高中范围」腾地方。这两条是实证最有效的，比泛泛说「写简洁些」可执行。

README 的目录表与「两条链」段落同步列出两份提示词；该文件另有在途改动（`打开Toolbox.bat` 相关），故与那批一起提交，不在本次范围内。

## 2026-11-22 — Windows 启动脚本拆分：只开网页 / 启动上传下载链路

原来只有 `启动云笔记工具.bat`，它把三件事串在一起：8088 静态服务、9222 调试 Chrome、`html2note.js --watch --check-cloud`。只想看网页（看题、下载、手选 PDF 上传）时，双击它也会留下一个后台 watch 进程持续轮询云笔记，既没必要也容易被误以为"网页有问题"。

现在拆成两个脚本，共用同一个 8088 服务：

| 脚本 | 启动内容 | 适用 |
| --- | --- | --- |
| `打开Toolbox.bat`（新） | 8088 服务（已在跑则复用）+ 普通浏览器窗口打开 `127.0.0.1:8088` | 只看网页 |
| `启动云笔记工具.bat`（原，行为不变） | 8088 服务 + 9222 调试 Chrome + `html2note.js --watch --check-cloud` | 需要自动转换/上传、云笔记同步 |

细节：

- `打开Toolbox.bat` **不碰 9222**，也不启动任何 `html2note.js`；起服务后会轮询端口（最多约 10 秒）再开浏览器，避免页面先于服务就绪打出 `ERR_CONNECTION_REFUSED`；
- 浏览器优先用 Chrome，找不到则退回系统默认浏览器；
- 两个脚本都是幂等的：8088 已监听只打印 `[skip ] 8088 already listening`，不会起第二个服务；交替双击哪个先哪个后都安全；
- 都保留 `/dry`（只打印动作）与"路径从 `%~dp0` 推导、必须放在仓库根目录"的约定；继续纯 ASCII + CRLF（cmd 按系统代码页解析，UTF-8 中文注释会乱码并被当命令执行）；
- `启动云笔记工具.bat` 的窗口标题、banner 与结尾提示改为"链路启动器"，并说明关掉 watch 窗口不等于关掉 8088 与调试 Chrome。

**验证**（本机 8088/9222 正在运行时）：

```text
打开Toolbox.bat /dry   → [skip ] 8088 already listening / [open ] http://127.0.0.1:8088/
启动云笔记工具.bat /dry → [skip ] 8088 / [skip ] 9222 / watch 判定为未运行 → DRY 只打印命令
```

两文件字节扫描无 >0x7F 字符，换行全部 CRLF。

## 2026-11-22 — 更新日志全量合并：上游历史 + 修改版 10/18 起全部记录

**两个来源**

| 来源 | 覆盖时间 | 取到什么 |
| --- | --- | --- |
| 早前在其他客户端的两份会话导出（`dsh-session-*.zip`，8120 + 8339 条事件） | 2026-10-17 ～ 11-10 | 每处修复的根因与结论，以及 11-10 生成的 `成果总览.md`（8 个新增模块的完整功能清单） |
| 本仓库 `CHANGELOG.md` | 2026-11-13 ～ 11-22 | 六轮改动的逐条记录 |

**合并结果**：`update.json` 从 30 条（24 上游 + 6 修改版）扩到 **37 条 / 109 个条目**，时间 2024/10/4 ～ 2026/11/22 连续无断档。

修改版部分新增 7 个日期（10/18、10/24、10/30、11/3、11/5、11/8、11/10），并按「新增 → 改进 → 修复 → 安全」给每条加了前缀，便于归类阅读：

- **10/18** 批量下载上线；图片加载因 ORB 被拦改用同源代理；图库卡死（观察者自触发）；专栏 404 改用本地壳页；
- **10/24** 老师分享只读页签、专栏登录与一键下载、云笔记独立窗口、移除 Beta/加速插件入口；
- **10/30** 图库下载把代理首页当图片（地址解析成空路径）；
- **11/3** PDF 上传改本地 pdf.js 渲染（摆脱 pdf2img 的 403 CORS）、模板内嵌、修「每页只剩上半部分」（1600×2263 溢出 2880×1800 画布）、修占位图水印；
- **11/5** 自动纠偏 + 逐页手动旋转；
- **11/8** 只读笔记流水线；修跨设备「获取文件位置失败」（二次上传缺 `page_router.bin`、仅首页有截图）；
- **11/10** 删 AES 常量、建 CHANGELOG、改说明致谢、去问卷。

**去重与纠错**（合并时发现并处理）：

- 11/13 原来把「移除问卷弹窗」又写了一遍——该弹窗 11/10 已删，11/13 删的是本地修改版里残留的 6.6 KB 首屏块，改为在 11/13 明确「本地修改版残留」；
- 「移除本地还原层」原记在 11/14，实际是 11/13（晚），已迁移；
- 11/13 补回此前漏记的五项：`serve.js` 幽灵 `console.log`、cdnjs 拉 `fabric.js` 阻塞登录、Bootstrap 5 与 `$.fn.modal` 不匹配、移除百度统计、补齐 `update.json`；
- 11/14 补回「去掉加载期对作者服务器的请求」与「巡检 5 秒被限流改回 10 秒」。

**展示**：折叠列表上方的分界说明改为「2026/10/18 起为本修改版的记录；2026/6 及更早为上游原始版本」。

**验证**（8088 实时）：折叠项 37 个，顺序 11/22 → 11/19 → 11/18 … → 2024/10/4；展开 11/13 得 11 条、11/14 得 7 条；`update.json` 为合法 JSON，日期严格升序、无重复。

## 2026-11-22 — 界面文案与项目现状对齐（说明 / 致谢 / 更新日志）

三处过时表述被纠正：

| 位置 | 原文（过时） | 现在 |
| --- | --- | --- |
| 登录页「说明：」 | 「本工具用于快捷查看中育账号资源…开发：Loshop…QQ群：1067807011」 | 说明这是基于 Loshop 上游的**修改版**、登录改为填学校代码直连学校接口、不经过第三方中转；上游问题不再引导到原作者群 |
| 说明与致谢 · 密钥条 | 「AES 主密钥不在仓库里，需在 config.local.js 配置」 | 中育 codeUtils 种子**已内置**在 `key-store.js`，克隆即可登录；只有**领创 KEY/IV** 需自备 |
| 致谢 | 「感谢各位的使用，我们有缘再会。开发：Loshop」 | 「感谢 Loshop 提供上游项目与原始实现；本版本在其基础上调整」 |

同时更新「使用说明」里与代码不符的描述：

- 画布尺寸 `2880×1800` → `2880×1759`（11-19 已按 App 可视区对齐），标签页与使用说明两处都改；
- 补上「竖版页转 90° 填满」开关（11-19 新增，默认开）；
- 「以下功能来自上游，本仓库未改动」→「只做了局部增强」，并注明新测评已有答案徽标/快捷对答案/起止时间、随身答已修复裂图。

更新日志数据 `update.json` 从 24 条补到 **30 条**，新增 2026/11/13–11/22 六条，记录本修改版的实际改动；页面上加了一行说明「2026/11 起为本修改版，2026/6 及更早为上游原始版本」。

验证（8088 实时，当前服务目录仍是 `repo-publish/web-toolbox`）：登录页说明四行文案正确；致谢两行正确；更新日志折叠项 30 个，首三条 2026/11/22、11/19、11/18；`2880×1800` 残留 0 处、`2880×1759` 2 处；无未捕获异常。

## 2026-11-22 — 移除「更新日志」自动弹窗；删掉「非学生账号功能受限」误报

**问题一：更新日志弹窗一直弹**

根本原因是这个功能**从未被真正删除**，而且即使只想「弹一次」也有三个漏洞：

| 原因 | 说明 |
| --- | --- |
| 两处触发 | `login()` 成功后调一次；另有一个 `DOMContentLoaded` 监听，只要 localStorage 里有 token，**刷新页面就再弹一次**。只删一处等于没删 |
| 关闭方式不写状态 | 只有点「我知道了」才调 `markUpdateAsRead()`；点右上角 × 或点遮罩关闭都不写 `lastViewedUpdate`，下次照弹 |
| 改错副本 | 8088 起的是 `E://WorkBuddy//中育Toolbox-独立版`，改 `repo-publish` 不会生效 |

处理：整块删除，不再保留「只弹一次」的语义——
- `index.js`：删掉 `login()` 里的调用、整个 `DOMContentLoaded` 监听、`parseDate` / `checkAndShowUpdateLog` / `loadUpdateLog` / `markUpdateAsRead` 四个函数（约 3.9 KB）；
- `index.html`：删掉 `#updateLogModal` 整块 DOM（约 1.1 KB）；
- 保留 `loadChangelog()` 与 `update.json`——「说明&致谢」页签是用户主动点击才加载的，属于文档而非弹窗，回归验证仍渲染 24 条。

**问题二：教师账号被误报「功能受限(没适配)」**

判据是 `accountVal[0] !== "2"`（账号首位不是 2 即非学生），独立版里还带着这行判断；而代码里**没有任何功能真的按账号类型做限制**，纯属误报。

- 三份副本统一删除该 `if` 块，不再弹这个提示；账号类型判断在全项目已无残留引用（`grep accountVal[0]` = 0）。

**同步范围**：`ZhongYuToolbox-main`、`repo-publish/web-toolbox`、`E://WorkBuddy//中育Toolbox-独立版`（8088 实际服务目录）。改前备份在 `.work/backup-20261122/`。

**验证**（8088 实时页面，伪造 token 后刷新等待 3 秒）：

```
hasModalNode: false | 显示中的弹窗: 0 | 遮罩: 0 | 页面文本含「更新日志」: false
loadChangelog 仍是 function | 说明页 accordion 子节点: 24 | 未捕获异常: 无
```

三份 `index.js` 均通过 `node --check`，`checkAndShowUpdateLog / markUpdateAsRead / updateLogModal / 非学生账号` 残留计数全部为 0。

## 2026-11-19 — 竖版页自动转 90° 填满（可选）；同步进独立版

App 实测（1914×1090 屏幕）：页面固定渲染为**屏高的 80%**（872px），在**整屏**居中（页中心 956 / 屏中心 957），左侧工具栏 134px 叠在上面，所以页面看起来偏左 68px、四周有 10% 格纹边距。**无论如何改画布比例都填不满屏幕**——那是 App 的缩放策略，不是页面尺寸问题。

在这种缩放模型下，真正影响观感的是页内留白：
- A4 竖版（0.707）放进横版画布，只占约 **44% 宽度**，左右各留 818px 白；
- 转 90° 后占满高度，左右只剩 **196px**，内容放大约 1.6 倍。

改动：

- 新增 `fillRotateDecision()`：页面是竖版、且画布是横版、且长短边比 ≥ `ROT_MIN_ASPECT`(1.15) 时返回 90°；横版页一律不动。
- 新增 `decidePageRotation()`：先按内容朝向判（内容侧躺才转正），结果为 0 时再叠加「填充优先」。
- 新增开关 `#pdf_fillrotate`（**默认勾选**），文字说明「文字会侧躺」；切换会重算缩略图默认角度，缩略图仍可逐页覆盖。
- 缩略图与正式渲染共用 `decidePageRotation()`，所以预览就是上传后的样子。

实测（测试 PDF 三页，2880×1759 画布）：

```
fill=on   第1页 0/1    第2页 196/197   第3页 196/197
fill=off  第1页 0/1    第2页 818/819   第3页 196/197
```

第 1、3 页是横版，两种情况都不变——策略只对竖版页生效。

**一处需要说清的事实**：自动旋转此前并没有坏。三页测试文档的内容本来就是正的，`pickPageRotation` 返回 0 是正确结果；另造一页「文字旋转 90°」的样本验证，`textAngle=90 → picked=270`，识别正常。这次是把「填满优先」作为**附加策略**加回来，而不是修 bug。

**独立版同步**（用户明确授权，仅本次）：`pdf-upload.js` 整体覆盖（已确认无独立版专有改动），`index.html` 只插入开关块、不动其它。改前备份在 `.work/backup-20261119/`。验证：8092 起独立版 → 画布 2880×1759、开关在且勾选、三页留白与上面 `fill=on` 一致。

## 2026-11-19 — 页面画布比例对齐 App 可视区（1800 → 1759）

拿 App 内真实截图（1914×1090）量了一遍：

| 量 | 值 |
| --- | --- |
| 整屏 | 1914 × 1090（1.7560） |
| 左侧浮动工具栏 | 宽 134px，占屏宽 **7.0%** |
| 顶部横条（页码 + 右上按钮） | 高 73px，占屏高 **6.7%** |
| 页面可视区（去掉左工具栏） | **1780 × 1087 = 1.6375** |

原来画布是 2880×1800（1.6000），与可视区差 2.3%。若 App 按「铺满」方式缩放，上下各会被裁掉约 2.3%（在 1087 高的视口里约 25px）——贴边的标题/页脚正好在最容易被切掉的位置。

- `CANVAS_H`：1800 → **1759**（2880 / 1.6375 ≈ 1759），比例 1.6373，与可视区基本吻合；
- 新增 `PAGE_SAFE = {left, top, right, bottom}`：按可视区比例预留安全边，默认全 0（画布已按可视区取比例，工具栏落在可视区之外）；若发现内容被工具栏压住，调 `left: 0.07 / top: 0.067`；
- 渲染改为「画布 − 安全边 − 留白 = 可用区，再按 `PAGE_FIT.alignX/Y` 摆放」，居中方式不再写死。

实测（真实 PDF，无头浏览器本地渲染）：

```
canvas 2880x1759 (1.6373) | image/webp | 53,906 字节
默认     内容包围盒 left 328 / right 331   → 左右对称，居中
安全边7%  left 504 / right 305             → 安全边生效
margin2%  left 374 / right 375             → 留白均匀
映射到视口后内容占宽 1373 / 1780（77%）
```

注意：模板 `page_router.bin` 是否硬编码了 1800 未能验证，建议先传一份笔记在 App 里确认，再批量重传。

## 2026-11-19 — PDF 判断改为校验文件头；页面排版参数化

**判断逻辑**

原来只看 MIME 或扩展名（`file.type.includes('pdf') || name.endsWith('.pdf')`），把 `.docx` 直接改名成 `.pdf` 也能过，要等 pdf.js 解析时才炸，报错还是 `Invalid PDF structure`。现在：

- 新增 `looksLikePdf()`：读前 1024 字节找 `%PDF-` 魔数（规范允许前面有少量字节，所以按偏移查找而不是只看开头）；
- 校验顺序改成 **空文件 → 超 50MB → 内容是否真 PDF → 放行**；
- 分别给出了两种提示：名字像 PDF 但内容不是（提示「另存为 PDF」）、完全不是 PDF（只说选错格式）；
- 被拒时清空 `input.value`，否则再次选同一个文件不触发 `change`，界面像卡死。

**WebP 是否强制**

不是强制，但**文件名写死**：

| 层 | 实际情况 |
| --- | --- |
| 编码 | `canvas.toBlob('image/webp', PAGE_FIT.quality)`，失败才回落 jpeg |
| 对象名 | `NOTE_PAGE_IMAGE` 固定以 `.webp` 结尾（模板 `page_router.bin` 里这么引用，改不了） |
| 服务端 | 不校验 content-type，OSS PUT 收什么都行 |

所以只有浏览器不支持 webp 编码时才会出现「jpeg 字节 + .webp 名字」的名实不符，已在回落分支加 `console.warn` 便于定位。实测：同一份 PDF 产出 `image/webp` / 55,916 字节。

**排版参数化**

页面图原来把 2880×1800、白底、居中、q0.92 全写死在渲染循环里。现在收成文件顶部的 `PAGE_FIT`：

```js
var PAGE_FIT = {
    margin: 0,        // 四周额外留白（占画布短边比例）
    bg: '#ffffff',    // 页面底色
    alignX: 'center', // left | center | right
    alignY: 'center', // top | center | bottom
    quality: 0.92
};
```

渲染处改为按 `margin` 收出可用区域、`pageAlignOffset()` 算对齐偏移；默认值（margin 0 / 白底 / 居中 / 0.92）与改动前几何一致。`CANVAS_W/H` 比例是 App 页面框的比例，注释里已标注「改比例会让 App 二次留白或被裁切」。

**验证**（真实 PDF，无头浏览器本地渲染）

```
canvas 2880x1800 | pages 1 | type image/webp | 55916 bytes | decoded 2880x1800
内容包围盒：left 302 / right 305（左右基本对称 → 居中生效）
```

## 2026-11-17 — 补齐「谁在读题作答」：新增外部 AI 交接提示词

`pipeline/tools/answer-pipeline/` 一直只负责作答**之后**的环节（渲染、校验、发布、队列），它自己的 README 也写明「它不是 AI 自动答题器」。但「上层怎么读图、按什么格式写稿」这件事此前只存在本地笔记里——别人克隆下来拿到的是一套没有说明书的工具，跑不起来。

新增 `docs/answer-pipeline-prompt.md`：

- 完整可复制的提示词：目录职责、双哨兵监测（`watch` 常驻 + `wait` 叫醒）、逐任务的 a~g 步骤、多题并发派发、草稿格式、版式硬约束、物理「教辅体」格式、禁止项、失败处理、交付说明；
- 路径按仓库结构重写：工作目录为 `pipeline/`，脚本仍是 `scripts/pipeline.py`（`pipeline.py` 的 `ANSWER_ROOT` 由脚本位置决定，所以从哪个目录调用都能定位 input/output）；
- 去掉了原稿里的本地绝对路径（`E:/WorkBuddy/题目`）、本机讲义目录（`新建文件夹/`）与私人草稿引用，改用仓库内 `tools/answer-pipeline/examples/demo_答案.md` 作为格式参照。

README 同步：目录表新增该文档；「两条链」一节补充「作答这一环由上层 AI 按提示词完成，不在代码里」。

克隆可用性实测：`git clone` 到干净目录 → `cd pipeline` → `python scripts/pipeline.py status`，返回「已记录素材：0 个 / 待作答任务：0 条」，无需预先创建任何目录。

## 2026-11-17 — 上传/云笔记成批失败：强制调试 Chrome 直连

现象：连续出现「OSS 上传失败（16/16）」与「云笔记巡检 fetch failed」，但同一台机器上本地页面（127.0.0.1:8088）一切正常，curl / Node 直连 OSS 也都返回 200，很容易误判成 DNS 或 OSS 侧故障。

根因是**系统代理**：注册表残留 `ProxyServer = 127.0.0.1:7890`（Clash 的默认混合端口），`ProxyOverride` 含 `127.*`、`10.*`、`192.168.*` 等绕过项。代理软件不在运行时，Chrome 仍按系统代理配置出网，页面内所有外网请求（OSS PUT、云笔记 API）全部连不上；而本地 8088 命中绕过名单始终可用，Node 与 curl 又根本不读系统代理，最终表现成"只有浏览器坏了"。

复现与验证：

| 调试 Chrome 配置 | 访问 OSS | 访问 127.0.0.1:8088 |
| --- | --- | --- |
| `--proxy-server=127.0.0.1:7890`（死端口） | `Failed to fetch` | OK 200 |
| `--no-proxy-server` | OK 200 | OK 200 |

改动：

- `启动云笔记工具.bat`：启动调试 Chrome 时增加 `--no-proxy-server`，不再依赖系统代理设置（脚本保持纯 ASCII 的约定不变）；
- `web-toolbox/pdf-upload.js` 的 `uploadMany()`：网络类错误（`Failed to fetch` / `NetworkError` / `ERR_*`）且整批全失败时，等 2 秒**整批重试一次**——并发 6 个请求会同时撞上同一故障，逐个重试没有意义；仍失败则在错误信息后追加代理排查提示，避免下次再从 DNS 查起；
- `README.md`：快速开始的手动启动命令补 `--no-proxy-server`，「已知限制」补充该约束。

## 2026-11-18 — 修复随身答图片全部加载失败（Referer 防盗链）

**现象**：随身答（答疑）列表卡片与「问题详情」弹窗的图片全裂，只剩浏览器默认的破损图标；同一页面里本地图标（`default-avatar.svg`、`folder.svg`）正常。

**定位过程**

1. 页面内统计：293 个 `<img>` 中 21 个 `naturalWidth === 0`，失败地址全是 `http://nswgy-alicdn.zyai.cc/quora_v2/res/...png`。
2. curl 直连该地址 → `200 image/png 515 KB`；**加上 `Referer: http://127.0.0.1:8088/` 后 → `403 text/html`**。
3. 页面内 `new Image()` 加载同一地址 → `onerror`，与 `<img>` 表现一致（页面自身 CSP 为空、无 `referrerpolicy` 设置）。

**根因**：阿里云 CDN 开了**防盗链白名单**，只放行学校域名等 Referer；页面跑在 `127.0.0.1:8088`，浏览器给每个 `<img>` 带上这个 Referer，于是全部被 403。跟路径拼接、缓存、权限都无关——curl 不带 Referer 就能拿到图。

**修复**

- `index.html` / `chem.html` 头部加 `<meta name="referrer" content="no-referrer">`：整个文档的子资源请求不再发 Referer，CDN 放行；实测接口调用（`Task/GetStudentTaskListAsync` 等）不受影响。
- `proxyImgSrc('')` 返回一张内联 SVG「该题目没有配图」占位：没有配图的问题不再显示裂图标。

**验证**：同一页面刷新后 `broken: 21 → 0`；`new Image()` 加载同一张图 `naturalWidth = 1921`；问题详情弹窗大图正常；列表头像、卡片截图（含手写照片）全部显示。

## 2026-11-18 — 测评阅读体验：答案徽标 / 快捷对答案 / 起止时间

新增 `web-toolbox/answer-view.js`（index.html 与 chem.html 都挂载），三处改动：

**1. 选项字母答案可视化**

平台返回的答案常常只是 `1. A` / `AB`，直接显示成一个孤立字母不好读。满足以下**全部条件**时换成「单选/多选」标签 + 圆形字母徽标 + 选项原文：

- 去掉标签、题号前缀（`1.` / `（2）`）与空白后，只由字母和分隔符组成；
- 1–6 个字母、互不重复、按 A→H 升序（`BAD` 这类倒序不当选）；
- 题干里能找到对应选项标记（`A.` / `A、` / `A．`）；
  **无题干**（`isNoStem` 答题卡类）时放宽为 1–4 个字母。

任一不满足 → 返回 `null`，调用方照原样输出，行为与改动前完全一致（`对`、`见解析`、`①③`、含公式的答案都不会被误判）。

**2. 快捷对答案**

测评详情里新增按钮，点击后只保留「题号.答案」，每行 5 题、顿号分隔：

```
1.A、2.D、3.C、4.C、5.A
6.B、7.B、8.D、9.B、10.B
```

再点「返回题目」切回原视图（原 DOM 备份在 `__examBodyBackup`）。

**3. 测评起止时间**

列表与详情都显示 `开始 / 结束`，统一格式 `YYYY-MM-DD HH:mm`；空值、`0001-01-01`、非法值统一显示 **待定**。列表沿用原 `list-group-item` 的 flex 布局（名称与时间包在一个 `flex:1 1 auto` 的容器里），不改变原有交互。

实测（真实账号，高三周练 20 题）：答案区 20 个徽标全部命中；快捷视图 4 行 × 5 组；列表里「11月18日-日常作业」无结束时间 → 显示「结束 待定」。

## 2026-11-18 — 让 clone 下来就能直接跑

以 `git archive HEAD` 导出（等同新克隆）实测，修掉这些「clone 即废」的点：

| 类别 | 问题 | 处理 |
| --- | --- | --- |
| 阻断 | `serve.js` 第 4 行有一句误插进注释的 `console.log(...PORT...)`，在 `const PORT` 之前 | 删除，服务正常启动 |
| 阻断 | `index.html` 引用的 `bg3.jpg` / `icon.png` / `fabric.min.js` / 三个 svg / `update.json` 不在仓库 | 全部入库（fabric 不再走 cdnjs） |
| 功能 | `index.js` 等仍有 23 处 `<relay-host>` 占位符 | 换回真实地址，学校代码登录可发现 API 主机 |
| 功能 | `config.local.js` 被 `index.html` 引用但仓库不提供 | `key-store.js` 内置中育种子；该文件变成只对「领创」必填，缺失只留一条 404 |
| 平台 | 只有 Windows 的 `.bat` | 新增 `start.sh`（`--watch/--status/--stop`，容器自动带 `--no-sandbox` 等） |
| 平台 | 没声明 Node 版本要求 | 新增 `package.json`（`engines: node>=21`，全局 `WebSocket` 需要） |
| 文档 | README 没写「装什么、跑哪条命令」 | 补三步启动；说明 `config.local.js` 可选 |

验证（HEAD 导出 + 无头 Chrome）：标题正常、学校代码输入框在、`aesEncrypt` 已挂载、`fabric` 可用、种子 60 字符、背景图 200；剩余 404 只有 `fonts.googleapis.com`（上游 CSS 的历史遗留）与 `config.local.js`（预期）。

## 2026-11-16 — 控制台输出带时间

原来只有写进文件的日志带 `[YYYY-MM-DD HH:MM:SS]`，CMD 窗口里是裸文本，盯窗口时看不出每轮是什么时候跑的。

- `runWatch()` 里的 console 包装增加 `[HH:MM:SS]` 前缀（`clock()`），文件仍由 `appendLog` 写完整日期，两边信息量不变。
- 实测：控制台 `[20:46:40] [云笔记] 无变化…`，文件 `[2026-11-16 20:46:40] [云笔记] 无变化…`。

## 2026-11-16 — 修正云笔记巡检节拍（配置 10 秒实际跑成 20 秒）

现象：`config.json` 写 `cloud.intervalMs: 10000`，启动日志也打印「每 10 秒一轮」，但日志里两轮间隔稳定是 **20 秒**（当天 2242 个间隔里 2121 个正好 20 秒）。

根因是间隔被算了两次：

```js
cloudTimer = setInterval(fn, CLOUD_INTERVAL);                    // 10s 跳一次
cloudNextAt = Date.now() + CLOUD_INTERVAL;                       // 结束时再加 10s
```

定时器下一次触发永远刚好差一点点（`now < end + 10s`），只能顺延到下下个 tick，于是稳定翻倍。

- 定时器改为 **1 秒一跳**，是否开跑完全由 `cloudNextAt` 决定；
- `cloudNextAt` 改成「**本轮开始时刻** + 间隔」（限流退避时才用「现在 + 退避」）。

实测（隔离目录、无头 Chrome、真实页面）：`intervalMs=5000` → 5 个间隔全部 5 秒；`intervalMs=10000` → 5 个间隔全部 10 秒。生产配置仍是 10000，重启 watch 后即为真正的 10 秒一轮。

## 2026-11-14 — 转换阶段少打一遍 PDF（-2.9s/文件）

上传并发生效后（2 页 15.2/27.6 s → **11.3 s**），瓶颈转到转换：日志里 `[转换] … 5321ms`，但单跑 `printToPDF` 只要 0.5–1.2 s。

根因在打印循环：`for (; tries < 3; tries++)` 里，长题干本来就会「1 个题块排成 2 页」，`okPages()` 的严格相等永远不成立，于是**白跑一轮** `sleep(250) + printToPDF + sleep(700)`，最后才走「多页也算过」的分支。

- 改为：只有 **页数比预期少**（真丢内容）才补打，`pages >= expectPages` 直接接受。最多 2 次，退避 400 ms。
- 实测同一份文件：`5321 ms → 2413 ms`。
- 上传并发 3 → 6（令牌已按笔记缓存，PUT 是直连阿里云，不会打到 `GenerateToken`）。2 页 32 个对象从约 11 批降到 6 批。

## 2026-11-14 — STS 令牌缓存：并发上传打爆 GenerateToken

并发上传上线后暴露出真正的限流点：**每传一个对象就调一次 `ObjectStorage/GenerateTokenV2Async`**（16 次/页）。串行时被摊开到 10 秒里没问题，改成并发后瞬间打出 3–5 个令牌请求，服务端直接 503：

```
获取 token 失败: {"code":503,"msg":"您的操作太频繁了，请稍后再试"}
OSS 上传失败（11/16）：…/a2b4fb47-…_file.bin
```

- `index.js` 的 `uploadFile()` 增加 STS 令牌缓存（`__ossTokenCache`，TTL 10 分钟）。缓存键 = 账号 token 尾部 + `fc` + **nonce**：STS 策略是围着这次 nonce（= 笔记的 customFileId）签的，带上它才能跨对象复用而不会 AccessDenied。效果：一篇笔记从 16 次令牌请求降到 **1 次**。
- `pdf-upload.js` 并发度 5 → 3，避免首轮突发。

## 2026-11-14 — 限流退避：5 秒巡检被 503，改 10 秒

把云笔记巡检改到 5 秒后，首次真实上传就撞了服务端限流：

```
[失败-上传] 20261114194509_ds_29_...  错误: GetAll 返回异常:
{"code":503,"msg":"您的操作太频繁了，请稍后再试"}
```

- 巡检间隔 **5 s → 10 s**（`CLOUD_INTERVAL` 默认与 `config.json` 的 `cloud.intervalMs` 同步改成 10000）。5 秒等于每分钟 12 次 `GetAll`，这个接口扛不住。
- **限流不再算失败**：新增 `withApiRetry()`，`GetAll` 命中 `操作太频繁 / 503 / 429` 时按 3 s→6 s→12 s 退避重试三次，上传前/上传后/巡检三处都走它。
- **上传期间巡检避让**：上传开始打点 `__lastUploadAt`，20 秒内不再跑云笔记轮询，避免两边同时压接口。
- **限流后自动降速**：巡检被 503 时把下一轮推迟 60 秒，而不是继续每 10 秒撞一次。

## 2026-11-14 — 上传并发化 + 云笔记巡检 5 秒

按实测（2 页 15.2 s / 2 页 27.6 s / 3 页 40.2 s）定位到瓶颈：`pdf-upload.js` 每页要传 16 个 OSS 对象（页面图 + 13 个模板含扁平副本 + 2 份截图），全是串行 `await`，每次 0.5–0.9 s。

- **上传并发化**：新增 `uploadMany()`（默认并发 5），把每页对象收集成列表后并发上传；路径、内容、resourceList 结构完全不变。合计 16 次串行 ≈ 4 批。
- **云笔记巡检 30 s → 5 s**：原来云笔记检查挂在 30 秒的 rescan 定时器上，只改 `CLOUD_INTERVAL` 不会生效；现改为独立 `cloudTimer`，并支持 `config.json` 的 `cloud.intervalMs` 配置（两个仓库/本机配置都写了 5000）。
- **轮询粒度 1000 → 250 ms**：缩略图就绪、上传进度两处；完成后固定 `sleep(1500)` 改为 400 ms。单个文件少等 ~2 s。
- 渲染侧未动（`render` 3–5 s、`check` 1.8 s、`printToPDF` 0.95 s，合计占比小）。

## 2026-11-14 — 去掉加载期对作者服务器的请求

隐私向调整：登录已改为「填学校代码 → 直连学校 API」，但页面加载时仍会碰作者的 `*.loshop.com.cn`。这次把这三处去掉：

- **专栏 / 选课 iframe 不再预加载作者域名**：`index.html` 里两个 `<iframe>` 的静态 `src` 由 `http://zyapi.loshop.com.cn/navPage.html?...` 改为 `about:blank`；点「在线专栏 / 选课」时由 `zxzl_set_url()` / `ck_set_url()` 用 `navSrc()` 现场赋值，指向当前学校的 `navPage.html`（`apiHost` 也换成当前 `API_BASE_URL`）。
- **下载中转探活改为惰性**：`detectLocalProxy()` 原来每次加载都探一次 `zytbdownloadagent.loshop.com.cn`，现改为「只有在本地代理与同源转发都不可用、且确实需要代理时才探一次」。
- **修掉「课表」按钮的学校硬编码**：原来写死 `sxz.school.zykj.org` + `apiHost=zyapi.loshop.com.cn`，换学校就串；改用 `window.navSrc("#/class")`，跟着当前登录学校走。

实测（8088 已登录页面）：加载期请求 = 本地静态 + 同源代理 + `nswgy-api.zyai.cc` ×2 + `127.0.0.1:5005` 探活，**`loshop` 命中 0**；点「在线专栏」后 iframe 指向 `https://nswgy.zyai.cc/navPage.html?apiHost=https://nswgy-api.zyai.cc&apiToken=...`。

仍会用到作者服务器的只剩「老师分享」（`zytbshareapi.loshop.com.cn`），只在主动点分享时请求，需要替代服务端才能真正去掉。

## 2026-11-14 — 补齐静态资源（背景图 / 图标）

**现象**：页面背景变纯白，标题栏还在但主体没有那张青色背景图。

**根因**：构建脚本的 `WEB_FILES` 只列了 js / html / css，`index.html` 里按相对路径引用的 `bg3.jpg`（整页背景）、`icon.png`（favicon）、`folder.svg` / `note.svg` / `Board.svg`（列表图标）从来没被复制进发布目录。8088 返回 404，浏览器就当没有背景。

- 已把这些资源补进 `web-toolbox/`，并把它们加进 `build-repo.js` 的 `WEB_FILES`，以后重建不会再漏。
- 验证：`/bg3.jpg` 200（92 KB）、`/icon.png` 200，页面该元素的 `background-image` 解析为 `url("http://127.0.0.1:8088/bg3.jpg")` 且正常绘制；加载期 4xx 只剩一条 `fonts.googleapis.com/...`（bootswatch 里被本地化的字体 import，回退系统字体，无影响）。

## 2026-11-14 — 修复跨天解密失败（页面 key 不刷新）

**现象**：云笔记巡检报 `[云笔记] 本轮出错：页面异常：SyntaxError: "undefined" is not valid JSON at JSON.parse ... :5:24`，反复刷屏。

**根因**：请求体 AES 的 key 由「种子串 + 当天日期」派生（`aeskey()` 里取 `new Date()`），但 `window.key` 只在页面加载时算了一次。页面开着过夜后，服务端已经换成当天的 key，页面仍用昨天的，`aesDecrypt()` 解密抛错并返回 `undefined`，紧接着 `JSON.parse(undefined)` 就报那句话。与 token、域名、网络都无关。

- `index.js` / `chem.js`：新增 `window.aesKey()`，每次 `aesEncrypt` / `aesDecrypt` 前按当天重算，日期没变则复用；跨天不必刷新页面。本地修改版同步修了同一处。
- `pipeline/html2note.js`：「本轮出错」时识别这类报错并附提示（页面 key 可能还是前一天的，刷新页面即可）。

实测：刷新页面后 `GetAll` 正常解密（648 条记录，其中标题含「ds」的 30 条），巡检恢复「无变化」。

## 2026-11-13（深夜）— 启动脚本改用真实流水线目录

- `启动云笔记工具.bat` 原来固定监视仓库里的 `pipeline/`（样例），现在改为**自动探测** `%PIPELINE_ROOT%`（默认 `E://WorkBuddy`）下第一个含 `html2note.js` 的目录；找不到才回退到仓库样例。底部信息会打印实际监听目录。
- 刻意**不**把中文目录名写进脚本：UTF-8 批处理里 `chcp 65001` 会让 cmd 的行读取按字节偏移错位，实测会报 `'65001' is not recognized` 与「无法初始化设备 PRN」，路径也解析不出来。自动探测既保持脚本纯 ASCII，又能命中中文目录。

## 2026-11-13（晚）— 改为不脱敏：仓库直接带真实接口地址

方向调整：之前为了公开分享做了脱敏，代价是克隆者必须先全局替换占位符才能登录。现改为**不脱敏**，目标是「克隆下来就能跑」。

- 域名还原：`index.js`、`chem.js`、`batch_pick.js`、`share_read.js`、`column_*.js`、`pdf-upload.js`、`linspirer.js`、`serve.js` 及几个 HTML 里的占位符换回真实主机（含 OSS 桶、中转与站点域名）。共约 180 处。
- 移除「本地还原层」：`serve.js` 不再读取 `.local/` 覆盖目录，`.gitignore` 里的对应规则一并删除。
- **中育 codeUtils 种子改为内置**（`key-store.js` 的 `DEFAULT_CODE_UTILS_SEED`），与上游公开仓库一致，克隆后无需配置即可登录；需要连别的服务端时仍可用 `config.local.js` 覆盖。
- **领创 KEY / IV 仍然不提供**：`key-store.js` 只保留读取与校验，未配置时抛明确提示；`config.local.example.js`、`docs/keys.md`、README 均已注明「需自己取得」。这类凭据进 git 历史即永久有效，不做妥协。
- 登录页改造：原来「学校」下拉只有 `sxz` 和「其它学校」，默认走 `sxz`。现改为**只填学校代码**的输入框，登录时用代码调 `hagateway.zykj.org/api/discovery/<code>` 拿到该校 API 地址；返回 http 的少数学校仍走 https 中转映射。学校代码记忆在 `localStorage.schoolCode` 里，下次自动回填。
- 注释中性化：删除「某代码 = 某学校」这类对照说明（如「江苏省锡中」「深圳市南山外国语」），`special` 路径相关注释改为「走中转时」。仓库里不再出现学校名称。
- 登录失败提示改为安全渲染（学校名来自服务端，原先是 `innerHTML` 拼接）。

## 2026-11-13 — 补齐脱敏与移除首屏问卷弹窗

- 脱敏补全：此前有两个漏过 `build-repo.js` RULES 的真实主机（一个 OSS 桶、一个厂商上游，含 880 / 883 端口）仍留在公开仓库里。现分别替换为 `<oss-bucket>.oss-cn-hangzhou.aliyuncs.com` 与 `linspirer-host.example.com`（合法占位域名，`new URL()` 不会报错），并把两者加进 RULES 与 leftovers 残留检查；涉及 `batch_pick.js`、`chem.js`、`index.js`、`linspirer.js`、`serve.js` 等文件。按 [docs/sanitization.md](docs/sanitization.md) 的约定，这里不复述原值。
- 首屏问卷弹窗：本仓库在 11-10 已删除（见下方记录）；这次把**本地修改版** `ZhongYuToolbox-main/index.html` 里 `body` 开头整段 6.6 KB 的 IndexedDB + `surveyModal` 脚本一并删除（含把 `localStorage.realName` 拼进外部问卷地址、`survey_completed` 消息监听）。发布版复核无残留，页面里的「调查问卷」只是学科下拉选项，未改动。
- 运行提示：`linspirer-host.example.com` 是占位符，使用领创模块前需替换回真实上游（可直接放 `.local/upstream.txt`，见下方「本地还原层」）；其余 `<api-host>` / `<oss-bucket>` 同理。
- **修复 `serve.js` 无法启动**：文件头注释块里混进了一行 `console.log("领创转发 ..." + PORT + ...)`（早前生成发布版时误插），导致 `ReferenceError: Cannot access 'PORT' before initialization`，服务直接退出。已删除该行，启动日志统一由 `listen` 回调打印。实测 `node web-toolbox/serve.js 8089` → `/linspirer/ping` 返回 pong、首页 200。
- **修复页面卡在加载中、无法登录**：`index.html` 第 27 行从 `cdnjs.cloudflare.com` 拉 `fabric.js` 是**阻塞式** `<script>`，该 CDN 不可达时整个解析停在这里，后面的 `index.js` / `linspirer.js` 全部不执行，表现就是「登录按钮点了没反应」。现把 `fabric.min.js`（5.3.1 构建，311 KB）内置到 `web-toolbox/`，改为 `<script src="fabric.min.js?v=1">`。实测断网条件下页面 `readyState=complete`、`fabric` 正常、登录按钮可用。
- **新增本地还原层**：`serve.js` 现在会优先发送 `web-toolbox/.local/` 下的同名文件（该目录已 gitignore）。把占位符换回真实域名的同名文件放进去，就能用同一份仓库代码在本机跑通登录，而提交上去的仍然是脱敏版；领创上游可另由 `.local/upstream.txt` 或环境变量 `LINSPIRER_UPSTREAM` 覆盖。启动时会打印「本地还原层：已启用」。
- **补齐 `update.json`**：页面启动会 `fetch('update.json')` 弹更新日志，但构建脚本的 `WEB_FILES` 一直漏了这个文件，发布版里是 404（控制台报「无法加载更新日志」）。已加入仓库并补进 `build-repo.js` 的文件清单。
- **Bootstrap 5 与 jQuery 插件不匹配**：仓库内置的是 Bootstrap 5.1.3（原生 JS，没有 `$.fn.modal`），而 `index.js` 里还有 22 处 `$(sel).modal('show'/'hide')`。其中一句在顶层执行，抛 `TypeError: $(...).modal is not a function` 会中断后续初始化（分享弹窗回调等绑不上）。已在 `index.js` 顶部加一个最小适配层，缺插件时转调 BS5 原生 API（modal/tab/collapse/tooltip/popover/carousel），不再抛错。
- **移除百度统计**：`index.html` 里 `hm.baidu.com` 的埋点脚本整段删除（本地修改版与发布版同步），页面现在不再向第三方发送访问数据。

## 2026-11-12 — 领创模块 405：中转域名停用，改走本地同源转发

**修复**：网页版点「登录并获取应用」报 `操作失败: HTTP 405:`。原公网中转域名已复用为「GForm 授权管理」站，nginx 只允许 `OPTIONS, GET`，POST 返回 405 `{"message":"Method Not Allowed"}`；真实上游 `https://linspirer-host.example.com:883/public-interface.php` 仍接受 POST（返回业务 JSON），但不返回 CORS 头，浏览器无法直连。

- 本地静态服务 `.work/serve.js` 新增 `/linspirer/<path>` 同源转发：保留 POST body 与 `Content-Type` 转发到上游，带 CORS、超时、16 MB 上限、最多 3 跳重定向跟随，目标 host 固定以避免变成开放代理；`/linspirer/ping` 用于探活。
- `web-toolbox/linspirer.js` 与本地版 `ZhongYuToolbox-main/linspirer.js`：默认端点改为 `/linspirer/public-interface.php`，保留直连上游作备用；链路级失败自动换端点，业务错误（`code != 0`）不重试；错误信息带上排查指引。`proxyUrl()` 的图标/下载链接随之映射到同源路径。
- 两份 `index.html` 的 `linspirer.js` 加 `?v=2` 破缓存。
- 验证：三份 JS `node --check` 通过；`OPTIONS` 预检 204、`/linspirer/ping` 返回 pong、POST 探针拿到上游业务 JSON；在 vm 里加载真实 `linspirer.js` 构造 AES envelope 走完整链路，返回的是上游业务错误而非「链路不可用」。**未代跑真实设备绑定**（`setdevice` 有服务端副作用）。

**追加修复（同日）— APK 下载到一半自动停止**：上一版转发把 API 响应的 `16 MB` 上限和 `20 s` 超时也套在了 GET 文件下载上，`中育桌面.apk`（32,694,904 B）与 `中育云笔记.apk`（76,095,224 B）传到一半被 `res.destroy()` 掐断，Chrome 报「请检查互联网连接状况」；只有 `文言文150背记.apk`（86,888 B）能下完。

- `/linspirer/` 的 GET/HEAD 改为纯流式 `pipe`：不计数、不主动超时，只有客户端断开才关闭上游；POST JSON-RPC 仍保留限流保护。
- 透传 `Content-Length` / `Content-Range` / `Accept-Ranges` / `Content-Disposition` / `ETag` / `Last-Modified` 等响应头，并把请求的 `Range` / `If-Range` / `If-Modified-Since` / `If-None-Match` 透传给上游，Chrome 的「继续」可真正断点续传。
- 验证：HEAD 返回 `content-length: 32694904` 与 `accept-ranges: bytes`；`Range: bytes=0-1023` 返回 `206` + `content-range: bytes 0-1023/32694904`；完整下载 31 MB APK 得到 32,694,904 字节（3.4 s，不再中断）；72 MB 文件尾部 Range 也返回 `206`。

**同日补充 — 让新 Toolbox 能独立跑起来**

- 新增 `web-toolbox/serve.js`：本地静态服务 + `/proxy/` 图片转发 + `/linspirer/` 领创转发（含 Range 断点续传与流式下载）。此前仓库里只有页面脚本，领创模块依赖的转发服务并不在仓库中，clone 后按旧说明用 `npx serve` 会 404。
- 新增根目录 `启动云笔记工具.bat`：Windows 一键启动 8088 服务、9222 调试 Chrome 与云笔记 watch，`/dry` 只打印动作；路径由 `%~dp0` 推导（必须放在仓库根目录），不含本机绝对路径与校内 OSS 主机。
- 该脚本改为**纯 ASCII 内容**：cmd 按系统代码页解析批处理，UTF-8 中文注释会变乱码并被当成命令，`rem` 里的 `<oss-host> <ip>` 尖括号还会被当成重定向而报「系统找不到指定的路径」。同时给 `serve.js` / `html2note.js` 加了存在性检查，放错位置时给明确提示。`/dry` 实测无乱码、无报错。
- README 的快速开始改用 `node web-toolbox/serve.js 8088`，目录表补上两个新文件。
- 清理 `index.html` / `pdf-upload.js` 的工作区行尾（仓库 `.gitattributes` 为 `* -text`，不自动转换），避免把数千行纯 CRLF/LF 差异混进提交。
- 「支持作者 / 支持原作者」页（`#Donate`）加上原作者的赞助链接 <https://ifdian.net/a/loshopwu>：本地版在原有打赏按钮下新增一段说明与「赞助原作者（爱发电）」按钮；发布版把爱发电设为主按钮、GitHub 仓库保留为次按钮，「说明与致谢」列表和分享弹窗文案同步。
- `pipeline/html2note.js` 开箱修复：`config.json` 被 `.gitignore` 排除，clone 后直接跑会 `ENOENT`；现在缺失时自动回退 `config.example.json` 并给出提示，`output/` 不存在时自动创建而不是抛错。启动脚本的 `WATCH_DIR` 改为显眼的可配置项（发布仓库里的 `pipeline/` 只是脱敏样例，真实流水线往往在别处）。

## 2026-11-11 — 上传笔记对齐应用版资源结构

**修复**：PDF 自动上传后的笔记在云笔记 App（1.13.4）里能显示，却无法导出或被别的应用打开。根因是出厂模板 bin 的 SHA-256 摘要链、模板目录层级、资源行 `md5` / `resourceType` 都与「中育Toolbox 应用版」不一致，详见 [docs/pdf-upload-fixes.md](docs/pdf-upload-fixes.md) §9。

- `web-toolbox/pdf-upload.js` 新增逐页模板摘要改写（`file.bin` → `router.bin`，且不污染缓存），恢复 `<uuid>/` 模板目录并保留页目录根的扁平兼容副本，每页截图同时放两处；9 条资源行改用真实 md5 与固定 `resourceType` 语义（1=模板 bin / 2=缩略图 / 0=页面图片）。
- `index.html` 中 `pdf-upload.js?v=14` → `?v=15`、`index.js?v=24` → `?v=25`，避免浏览器沿用旧缓存。
- `index.js` 的「下载笔记」按资源真实扩展名落盘（OSS 上是 webp，旧代码一律写 `.jpg`，按扩展名解码的应用会报文件损坏）。
- 同步修改 `ZhongYuToolbox-main/` 下的同源文件（两份仅脱敏占位符不同）。
- 自检脚本直接加载文件中的真实函数、喂 `example/` 下的 bin 做 12 项断言（摘要替换、长度不变、matrix/command 摘要保留、缓存未污染、9 条资源行等），12/12 通过；两份脚本 `node --check` 通过。真机「导出 PDF / 用其他应用打开」仍需人工确认一次。

## 2026-11-10 — 发布准备

**安全**

- 删除全部 **AES 主密钥常量**：
  - `index.js` / `pdf-upload.js` / `chem.js` 里 codeUtils 的种子串（`generateAesKey` / `aeskey`）；
  - `linspirer.js` 的 `LINSPIRER_KEY` / `LINSPIRER_IV`。
  改为运行时读取 `window.CODE_UTILS_SEED` / `window.LINSPIRER_KEY` / `window.LINSPIRER_IV`；
  新增 `web-toolbox/key-store.js`（只读取与校验）与 `web-toolbox/config.local.example.js`（模板）；
  `config.local.js` 进 `.gitignore`。未配置时抛错而不是静默算出乱码。详见 [docs/keys.md](docs/keys.md)。
- 机械脱敏：校级 API 主机、中转主机、pdf2img 服务、站点域名、两个 OSS 桶、学校代码、账号名、用户数字 id 全部替换为占位符；只覆盖自己写的文件，第三方库原样保留。详见 [docs/sanitization.md](docs/sanitization.md)。
- 未入库：桌面客户端 `app.asar` / exe、APK 样本、抓包目录、Chrome 调试 profile（含登录 token）、`logs/`、`.workbuddy/` 状态、真实笔记与答卷；只保留 `sample/` 下的一份脱敏样例。

**修复**

- `linspirer.js` 副本的中文注释整片乱码（`棰嗗垱` 之类），从原始源码重新生成。
- `pipeline/html2note.js` 里 92 个 `U+FFFD` 残留清理：删掉 34 行损坏注释，恢复 3 条日志文案。
- 去掉首屏的**问卷调查弹窗**（`index.html` 里 `body` 开头的整段 IndexedDB + Modal 逻辑，约 6.6 KB）：它会在每次打开页面时弹「问卷调查邀请」，并把 `localStorage.realName` 拼进外部问卷地址打开（`wj.html?realName=…`）；同时删掉配套的 `surveyModal` 模板与 `survey_completed` 消息监听。原仓库该功能为上游所有。
- 页面内的「使用说明」整节重写（3,783 → 3,221 字符）：按现有 15 个页签改写（云笔记三个子页签、预览/导出 PDF/一键下载、老师分享、在线专栏单篇与批量、化学卷、领创、上游页签、自动化流水线、说明与致谢）；PDF 上传页签里过时的「打包 ZIP / 上传转换」提示同步换成当前行为（本地渲染、逐页旋转、保持前台）。

**工程**

- `.gitattributes` 设 `* -text`：`example/*.bin` 模板必须字节一致，否则资源行的 md5 对不上（校验 `page_router.bin` SHA-256 与源文件一致）。
- 新增 `docs/keys.md`、`docs/sanitization.md`、`docs/pdf-upload-fixes.md`、`docs/pipeline.md`、`docs/web-toolbox.md`、`docs/notes/2026-11-08.md` 与 `sample/`。

## 2026-11-09 … 11-10 — 作答链路并入

- 纳入另一条链路的产物：`pipeline/scripts/pipeline.py`（22 行兼容入口，设置 `ANSWER_ROOT` 后调用包内 `cli:main`）与 `pipeline/tools/answer-pipeline/`（Python 包，含 4 个单元测试）。
- 10 个子命令：`init` / `status` / `scan` / `watch` / `wait` / `render` / `check` / `publish` / `done` / `skip`。
- `python scripts/pipeline.py --help` 只依赖标准库即可运行，已实测。

## 2026-11-08 — PDF 上传链路 8 处修复

1. **卡在 15% 不报错**：标签页在后台被 Chrome 降频，`page.render()` 的 promise 永不 resolve。加 `PDF_RENDER_TIMEOUT_MS = 60000` 超时与明确文案；测试脚本侧用 `Emulation.setFocusEmulationEnabled` 复现/验证（273 ms 完成 vs 132 s 无响应）。
2. **pdf.js worker 走外网**：`workerSrc` 指向 `cdnjs` 的 `pdf.worker.min.js`，跨域 `new Worker` 必被拒（`SecurityError`），退回 fake worker 仍要抓外网脚本。改为本地 `pdf.worker.min.js`。
3. **自动旋转把所有纵向页转 90°**：旧判据只看「横版画布填充率」，A4 纵向页比值 2.0 恒超阈值。改为「文字基线角度优先，纯图页看非白像素的行列分布，判不出就 0」，并加**逐页手动旋转**缩略图条（← → 每次 90°，可全转/复位）。
4. **`Access denied by authorizer's policy.`（403）**：客户端用 **UTC** 日期拼 OSS key，服务端 STS 策略按**北京时间**取 `yyyyMMdd`，每天 00:00–08:00 差一天。改用 `Asia/Shanghai` 推导日期，捕获策略拒绝且两日期不同时用 UTC 重试一次，并把实际日期回传给后续 `fileUrl`/`saveNote` 保持同一天。
5. **手机端「获取文件位置失败」**（四个子问题，App 按 `fileUrl + 页目录 + 文件名` 取文件）：
   - `saveNote` 的 `fileUrl` 主机写死成另一个桶 → 改为按 `window.ossBaseUrl` 生成；
   - 7 个模板与 `snapshot.bin` 原来共用 `ossPageHash/<uuid>/` 目录，与每页 `pageName` 不一致 → 改为每页上传到自己的 `pageHash/` 目录；
   - `loadTemplateFiles()` 返回缓存对象，首次上传时 `delete templates['page_router.bin']` 导致**同一会话第二次上传缺该文件** → 不再删除缓存；
   - 只有第 1 页上传 `screenshot.png`，后续页的截图行指向不存在的对象 → 每页都上传自己的 PNG。
6. **导出的 PDF 每页都是第 1 页**：导出循环取 `pageData.thumbnail`（`resourceType == 2` 的整篇截图）当每页图。改为取该页 `originals` 里的 webp/PNG，并用 `imgFormat()` 按 dataURL 的 MIME 传格式（原来硬编码 `'JPEG'`）。
7. **「下载笔记」按钮永久失效**：`noteDownload2` 把 URL 写死成 `${API_BASE_URL}/special/GetByFileId`（只对中转站点有效），失败后 `this.downloading` 不释放。改用 `getCloudNoteApiPathR()` 并 `try/finally` 复位标志。
8. **模板 `.bin` 被当成图片处理**：下载端按扩展名挑选、放宽 MIME 校验，避免 `application/octet-stream` 被拒。

## 2026-11-08 — HTML → PDF → 云笔记 流水线

- `convertOne`：`file://` 打开 → 等 `readyState` → `Emulation.setEmulatedMedia('print')` → `.page` 个数自检（无 `.page` 时用 `break-before: page` 元素数，容差 ±1）→ `Page.printToPDF`（`preferCSSPageSize` / `printBackground` / 零边距）→ 数 `/Type /Page`。
- `uploadPdf`：注入 `File` 到 `#pdfFileInput` → 等「共 N 页」→ 写标题 → 点上传 → 轮询进度 → `GetAll` 差集认领新笔记的 `fileId`。
- `--watch`：`fs.watch` + 1 s 防抖 + 30 s rescan + 单并发队列 + 内容哈希去重 + `logs/watch-YYYY-MM-DD.log` 按天切分 + `SIGINT` 优雅退出。
- `--check-cloud`：巡检云端目录，按标题关键字过滤，把新笔记（含资源）下载到 `input/`，去重记录在 `.workbuddy/cloud-seen.json`（键 = `fileId`）；**对云端零写操作**。
- `ensureAuth`：`token` 未过期直接用 → `refreshToken` 续期 → 账号密码登录；凭据只从 `config.json` 或环境变量读，不落盘不打日志；`--check-auth` 可单独验证。

## 2026-11-07 … 11-08 — 网页端扩展模块

- 新增 `batch_pick.js`（批量下载）、`share_read.js`（老师分享）、`column_read.js` / `column_batch.js`（专栏）、`pdf-upload.js`（PDF/图片转笔记，含逐页缩略图与手动旋转）、`chem.js` + `chem.html`（化学卷）、`linspirer.js`（领创模块）、`cloudnote-window.html`（独立笔记窗口）。
- 统一用 `?v=` 查询串破缓存，模块间复用页面内的 `uploadFile` / `saveNote` / `aesEncrypt` / `localStorage.token`。

## 基线

- 基于上游 [BennyLoshop/ZhongYuToolbox](https://github.com/BennyLoshop/ZhongYuToolbox) 的网页版工具箱（`index.html` / `index.js` / pdf.js 与模板）。
