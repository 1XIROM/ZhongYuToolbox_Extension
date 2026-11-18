# 密钥处理

本仓库对两类密钥的处理**不一样**，别混在一起看。

| 密钥 | 用途 | 仓库里的状态 |
| --- | --- | --- |
| 中育 codeUtils 种子 | 云笔记接口请求体的 AES 密钥由它 + 当天日期派生 | **已内置**（`key-store.js`），与上游公开仓库完全一致 |
| 领创 `LINSPIRER_KEY` / `LINSPIRER_IV` | 领创模块接口的 AES-CBC 密钥与偏移 | **不提供**，必须自己准备 |

## 为什么中育那把可以直接内置

上游项目（`BennyLoshop/ZhongYuToolbox`）的 `index.js` 里明文写着这把种子，仓库是公开的——也就是说它早就不是秘密了。把它再藏起来只会让克隆者从上游另找一份，除了增加摩擦没有别的作用，所以这里直接内置。

如果你连的是别的服务端、种子不同，可以在 `config.local.js` 里覆盖：

```js
window.CODE_UTILS_SEED = "<你的种子串>";
```

## 为什么领创那把不能放进来

它不在任何上游代码里（原版没有领创模块），拿到它就能以官方客户端的身份调领创接口、配合设备号推导设备管理员密码。这类凭据一旦进了 git 历史就**永久有效且撤不回来**（对象不可变 + fork/镜像/缓存），所以：

```
web-toolbox/
├── key-store.js             ← 入库：读取与校验，只含中育种子的默认值
├── config.local.example.js  ← 入库：模板，领创两项留空
└── config.local.js          ← 不入库（.gitignore），你自己填
```

`index.html` 在 `crypto-js.js` 之后、业务脚本之前加载它们：

```html
<script src="key-store.js"></script>
<script src="config.local.js"></script>
```

业务脚本通过这三个函数取用：

```js
window.readCodeUtilsSeed()   // → 内置默认值，或被 config.local.js 覆盖
window.readLinspirerKey()    // → 未配置时抛错
window.readLinspirerIv()     // → 未配置时抛错
```

## 只想要中育功能（不用领创）

什么都不用做，克隆后直接跑。

## 想用领创模块

```bash
cd web-toolbox
cp config.local.example.js config.local.js   # Windows: copy config.local.example.js config.local.js
```

```js
// config.local.js —— 只填这两项
window.LINSPIRER_KEY = "<16 字节>";
window.LINSPIRER_IV  = "<16 字节>";
```

**这两个值从哪里来**：仓库不提供，也不在文档里给，需要你自己从所连接的领创环境中取得（例如客户端、设备管理端或抓包）。拿不到就先别用领创模块——页面上该模块会明确提示「缺少 LINSPIRER_KEY」，其它功能不受影响。

确认它没被跟踪：

```bash
git check-ignore -v web-toolbox/config.local.js
```

## 影响

- 不填 `config.local.js`：中育登录、云笔记、专栏、PDF 上传、批量下载都正常；只有领创模块报缺 KEY；
- 网页版与 `pipeline/html2note.js` 共用页面内的加密函数，所以中育种子对流水线同样生效。

## 万一误提交了领创 KEY

按已泄露处理：

1. 从当前提交里删掉，`git filter-repo`（或 BFG）重写历史后强推；
2. 检查 fork、CI 缓存、发布压缩包里是否还有副本；
3. 联系托管方清缓存——但默认认为它已经公开，转而收窄其它可利用面（设备号、账号）。
