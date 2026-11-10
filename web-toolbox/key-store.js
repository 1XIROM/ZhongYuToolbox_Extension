// ==================== 运行时密钥载体 ====================
// 仓库里不再包含任何 AES 主密钥。业务脚本通过这里的函数取值，
// 值来自 config.local.js（不入库）里的 window.CODE_UTILS_SEED / window.LINSPIRER_KEY / window.LINSPIRER_IV。
// 未配置时抛错并给出提示，而不是静默用错密钥算出乱码。
(function () {
    function need(name, value) {
        if (typeof value !== "string" || value.length < 8) {
            throw new Error(
                "缺少 " + name + "：请把 config.local.example.js 复制为 config.local.js 并填写后，" +
                "在 index.html 里先于业务脚本加载（见 docs/keys.md）"
            );
        }
        return value;
    }
    window.readCodeUtilsSeed = function () {
        return need("CODE_UTILS_SEED", window.CODE_UTILS_SEED);
    };
    window.readLinspirerKey = function () {
        return need("LINSPIRER_KEY", window.LINSPIRER_KEY);
    };
    window.readLinspirerIv = function () {
        return need("LINSPIRER_IV", window.LINSPIRER_IV);
    };
})();
