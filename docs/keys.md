# 密钥处理：AES 主密钥已从仓库移除

上游项目把加解密所需的密钥直接硬编码在脚本里。本仓库把这些常量全部删除，改成**运行时注入**。

## 删掉了什么

| 位置（上游） | 常量 | 用途 |
| --- | --- | --- |
| `index.js`、`pdf-upload.js`、`chem.js` | `var e = "<种子串>"`（`generateAesKey` / `aeskey` 里） | 云笔记接口请求体的 AES-ECB 密钥由它 + 当天日期派生 |
| `linspirer.js` | `LINSPIRER_KEY` / `LINSPIRER_IV` | 领创模块接口的 AES-CBC 密钥与偏移 |

这些是**平台侧的共享密钥**，不是本机配置。跟着源码公开，任何人都能解开或伪造加密请求，所以不该出现在公开仓库。

## 现在怎么取密钥

```
web-toolbox/
├── key-store.js             ← 入库：只负责读取与校验，本身不含任何密钥
├── config.local.example.js  ← 入库：模板，值全为空
└── config.local.js          ← 不入库（.gitignore），你自己填
```

`index.html` 与 `chem.html` 在 `crypto-js.js` 之后、业务脚本之前加载这两个文件：

```html
<script src="key-store.js"></script>
<script src="config.local.js"></script>
```

`key-store.js` 暴露三个函数，业务脚本改成调它们：

```js
window.readCodeUtilsSeed()   // → CODE_UTILS_SEED
window.readLinspirerKey()    // → LINSPIRER_KEY
window.readLinspirerIv()     // → LINSPIRER_IV
```

未配置时会抛出带指引的错误（例如「缺少 CODE_UTILS_SEED：请把 config.local.example.js 复制为 config.local.js 并填写」），而不是拿空串算出一堆乱码。

## 你要做的三步

```bash
cd web-toolbox
cp config.local.example.js config.local.js   # Windows: copy config.local.example.js config.local.js
# 编辑 config.local.js，填入你自己的值
```

```js
// config.local.js
window.CODE_UTILS_SEED = "<你的种子串>";
window.LINSPIRER_KEY   = "<16 字节>";
window.LINSPIRER_IV    = "<16 字节>";
```

确认它没被跟踪：

```bash
git check-ignore -v web-toolbox/config.local.js
```

## 影响

- 不填 `config.local.js`：页面能打开、能浏览，但凡是调用接口加密的功能（云笔记请求、领创模块）都会报错并提示缺哪个值；
- 填了正确的值：行为与上游一致；
- 本仓库的 PDF 上传流水线同样走页面内的加密函数，所以也需要这份配置。

## 如果你的副本里曾经提交过这些常量

把它们当作已泄露处理：

1. 让服务端轮换密钥 / 令牌；
2. 用 `git filter-repo`（或 BFG）从历史里抹掉，再强推；
3. 检查 fork、CI 缓存、发布的压缩包里是否有残留副本。
