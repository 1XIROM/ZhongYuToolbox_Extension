// 复制本文件为 config.local.js，填入自己的密钥（config.local.js 已在 .gitignore 中）。
//
// 只有「领创（Linspirer）」的 KEY / IV 是必填项 —— 仓库不提供，需要你自己从所连接的
// 领创环境里取得。中育的请求体种子已内置在 key-store.js（与上游公开仓库一致），
// 除非你要连别的服务端，否则不用填。
window.CODE_UTILS_SEED = "";   // 可选：中育云笔记接口请求体 AES 的种子串，留空则用内置默认值
window.LINSPIRER_KEY = "";     // 必填：领创模块 AES-CBC 密钥（16 字节），拿不到就别用领创模块
window.LINSPIRER_IV = "";      // 必填：领创模块 AES-CBC 偏移（16 字节）
