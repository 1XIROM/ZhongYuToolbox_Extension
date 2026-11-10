# 脱敏说明

本仓库在发布前对原始文件做了一次机械替换：所有**校内接口主机、对象存储桶、学校代码、账号名**都被换成了占位符。为了不把原始值再写回来，这里只列**类别**，不复述原值。

| 类别 | 替换为 |
| --- | --- |
| 校级 API 主机（笔记 / 资源 / 鉴权） | `<api-host>` |
| 中转主机（网页走的那层转发） | `<relay-host>` |
| PDF 转图服务 | `<pdf2img-host>` |
| 站点主机（水印里的域名） | `<web-host>` |
| 对象存储桶（两个 bucket，杭/深各一） | `<oss-bucket>.oss-cn-<region>.aliyuncs.com` |
| 学校代码 | `example-school` |
| 账号名 | `example-user` |
| 用户目录里的数字 id | `userNote/<userId>` |
| **AES 主密钥**（codeUtils 种子串、领创 KEY/IV） | **已删除**，改为运行时从 `config.local.js` 读取 |

替换范围只覆盖**我们自己写的**文件（`web-toolbox/*.js|html|css`、`pipeline/*.js|py`、`docs/*.md`）；第三方库（`jquery`、`jspdf`、`jszip`、`pdf.js`、`katex` 等）原样保留，它们内部的默认域名与业务无关。

> 说明：厂商产品名（如页面标题、`com.*` 包名）与英文公司名按原样保留——它们是产品标识而不是内网地址，去掉会让代码无法和上游版本对照。

## 没有进入仓库的内容

- 客户端程序（`app.asar` / 桌面客户端 exe）、APK 样本、抓包目录；
- Chrome 调试 profile（里面存着登录 token）；
- `logs/`、`.workbuddy/` 状态文件（含真实 `fileId` 与本机绝对路径）；
- `input/`、`output/` 里的真实笔记与答卷——只保留 `sample/` 下一份脱敏样例；
- APK 漏洞清单与利用脚本。

## 你要补什么才能跑

1. `web-toolbox/` 里所有 `<api-host>` / `<oss-bucket>` / `<relay-host>` 占位符，替换成你自己的部署值（主要出现在 `index.js`、`pdf-upload.js`、`batch_pick.js` 与几个 HTML）；
2. 把 `pipeline/config.example.json` 复制成 `config.json`，按需改路径；
3. 需要自动重登时，再往 `config.json` 加 `"auth": {"userName": "...", "password": "..."}`（或用环境变量 `ZY_USERNAME` / `ZY_PASSWORD`）——**这个文件已在 .gitignore 里，别提交**。

## 顺带修掉的编码残留

`pipeline/html2note.js` 曾因一次 PowerShell 事故（用 `Get-Content | Set-Content -Encoding utf8` 改写含中文的 UTF-8 文件）产生乱码。仓库里的副本已清理 `U+FFFD` 残留：损坏的注释行被删除，3 条日志文案恢复为正常中文。本地原始文件未做改动。
