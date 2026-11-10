# 样例

## input-note/

这是「从云端下载回来的一条笔记」的其中一页，用来示意输入格式。真实下载目录里每页有 9 个文件：

```text
page_router.bin                                      # 页路由（40 字节）
header.bin / router.bin / snapshot.bin               # 画布元数据（百字节级）
a888b5fb-…/059848e4-…_matrix.bin                     # 变换矩阵
a888b5fb-…/a2b4fb47-…_file.bin                       # 文件索引
a888b5fb-…/e339e39b-…_command.bin                    # 绘制指令
screenshot.png                                       # 整页缩略图（resourceType=2）
B466246B…screenCapture….webp                        # 整页位图（正文内容）
```

这里只保留 `page-1.webp`（正文）与 `thumbnail.png`（缩略图），其余 `.bin` 是几十到几百字节的元数据。

## output-answer/

一条流水线产出的题目 HTML（单文件、无外链脚本、可直接打印成 A4 横向）。它就是 `pipeline/html2note.js` 的输入：

```bash
node pipeline/html2note.js --dry --only answer-page
```