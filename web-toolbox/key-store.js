// ==================== 运行时密钥载体 ====================
// 分两类，处理方式不同：
//
// 1) 中育 codeUtils 种子（请求体 AES）：与上游公开仓库一致，直接内置，开箱即可登录。
//    想换成自己服务的种子，在 config.local.js 里设 window.CODE_UTILS_SEED 覆盖即可。
// 2) 领创（Linspirer）KEY / IV：**仓库里不提供**，必须自己准备，放在 config.local.js。
//    未配置时这里抛错并给出提示，而不是静默用错密钥算出乱码。
(function () {
    var DEFAULT_CODE_UTILS_SEED = ":F0wKU!Qg3}UkbW+w[:9|D3-5h=:T;7t#_GZ4#G;~ZNSq{8;}QIP>'{q.lje";

    function need(name, value) {
        if (typeof value !== "string" || value.length < 8) {
            throw new Error(
                "缺少 " + name + "：领创模块的 KEY/IV 不在仓库里，请把 config.local.example.js 复制为 " +
                "config.local.js 并填写后，在 index.html 里先于业务脚本加载（见 docs/keys.md）"
            );
        }
        return value;
    }

    window.readCodeUtilsSeed = function () {
        var s = window.CODE_UTILS_SEED;
        if (typeof s === "string" && s.length >= 8) return s;
        return DEFAULT_CODE_UTILS_SEED;
    };
    window.readLinspirerKey = function () {
        return need("LINSPIRER_KEY", window.LINSPIRER_KEY);
    };
    window.readLinspirerIv = function () {
        return need("LINSPIRER_IV", window.LINSPIRER_IV);
    };
})();
