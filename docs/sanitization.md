# 域名与学校信息的处理（方案：不脱敏）

**当前状态：仓库里写的是真实接口地址，没有做脱敏。** 目的是「克隆下来就能跑」。

早期版本曾经把校内主机、OSS 桶、学校代码全部替换成占位符（`<relay-host>` / `<oss-bucket>` / `example-school` …），代价是克隆者必须先做一次全局替换才能登录，实际体验很差。现在改为直接保留真值。

## 仓库里有什么、没有什么

| 内容 | 在仓库里 | 说明 |
| --- | --- | --- |
| 学校 API 主机、中转服务器、OSS 桶、站点域名 | ✅ 真值 | 属于学校自有信息，公开分享前请自行判断 |
| 学校代码（登录用） | ✅ 真值 | 登录页要求填写，代码本身不是凭据 |
| 学校名称与代码的对照 | ❌ 已删除 | 注释里不再出现「某代码=某学校」这类映射 |
| 中育 codeUtils 种子 | ✅ 内置 | 与上游公开仓库一致，藏起来没有意义 |
| 领创 KEY / IV | ❌ 不提供 | 需自备，见 [keys.md](keys.md) |
| 账号、密码、token | ❌ 不提供 | 由使用者自己登录获得 |

## 不会进入仓库的内容

- 客户端程序（`app.asar` / 桌面客户端 exe）、APK 样本、抓包目录；
- Chrome 调试 profile（里面存着登录 token）；
- `logs/`、`.workbuddy/` 状态文件（含真实 `fileId` 与本机绝对路径）；
- `input/`、`output/` 里的真实笔记与答卷——只保留 `sample/` 下一份样例；
- APK 漏洞清单与利用脚本；
- `web-toolbox/config.local.js`（领创密钥）、`pipeline/config.json`（可能含账号密码）。

## 如果你打算公开分发这份代码

先自己脱敏一遍。旧的替换规则仍保留在本地构建脚本里（`build-repo.js` 的 `RULES`，仓库外），主要替换：

| 类别 | 建议替换为 |
| --- | --- |
| 校级 API 主机 | `<api-host>` |
| 中转主机 | `<relay-host>` |
| PDF 转图服务 | `<pdf2img-host>` |
| 站点主机 | `<web-host>` |
| 对象存储桶 | `<oss-bucket>.oss-cn-<region>.aliyuncs.com` |
| 学校代码 | `example-school` |

注意：替换后要连带把 `index.js` 里由 API 主机反推 web 主机的逻辑（`navWebFromApi`）一起考虑，否则专栏 iframe 会指向占位域名。

## 你需要补什么才能跑

1. `web-toolbox/config.local.js`：只需要填领创的 `LINSPIRER_KEY` / `LINSPIRER_IV`（见 [keys.md](keys.md)）；中育种子已内置；
2. `pipeline/config.example.json` 复制成 `config.json`，按需改路径；
3. 需要自动重登时，再往 `config.json` 加 `"auth": {"userName": "...", "password": "..."}`（或环境变量 `ZY_USERNAME` / `ZY_PASSWORD`）——**该文件已 gitignore，别提交**。

## 顺带修掉的编码残留

`pipeline/html2note.js` 曾因一次 PowerShell 事故（`Get-Content | Set-Content -Encoding utf8` 改写含中文的 UTF-8 文件）产生乱码。仓库里的副本已清理 `U+FFFD` 残留：损坏的注释行被删除，3 条日志文案恢复为正常中文。
