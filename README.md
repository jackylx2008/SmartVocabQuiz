# SmartVocabQuiz

本地英语词汇扫描与组卷工作台。代码仓库：[jackylx2008/SmartVocabQuiz](https://github.com/jackylx2008/SmartVocabQuiz)。

运行根目录入口脚本后，会在浏览器打开本地 HTML 功能选择页，提供 **扫描词汇** 和 **生成试题** 两个入口。扫描词汇可以查看扫描状态、扫描新增 PDF、搜索词库及下载 JSON；生成试题使用本地 AI 和词库组合 15 道练习题，供家长或教师逐题审核，再将勾选的题目导出为 A4 PDF。

## 安装与配置

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item common.env.example common.env
```

在 `common.env` 中填写已运行的本地模型服务 URL、模型名和 API Key。程序只连接现有服务，不启动或关闭 AI 服务。`config.yaml` 设置输入目录、输出文件、扫描记录文件、模型请求参数和网页端口。接口与配置说明见 [本地 AI 配置](docs/LOCAL_AI_SETUP.md)。`common.env`、`BookScan/`、`logs/`、`output/` 已列入 `.gitignore`。

## 打开页面

```powershell
.\.venv\Scripts\python.exe smart-vocab-quiz.py
```

脚本会启动只监听 `127.0.0.1` 的网页服务并自动打开功能选择页；按 `Ctrl+C` 停止网页。使用 `--no-browser` 可以只启动服务，`--port 8765` 可以固定端口。默认使用系统分配的空闲端口。选择页的两个按钮分别进入扫描工作台和生成试题页，两个页面均可返回功能选择页。

页面右上角显示本地 AI 状态，并可点击重新检测。状态检查只读取现有服务，不会启动或关闭它。AI 未启动或配置模型不可用时，点击首页的 **扫描词汇** 或 **生成试题** 会弹出启动提示；启动服务后点击弹窗中的 **重新检测**，即可继续进入所选页面。子页面顶部的 **功能选择** 控件可返回首页。

在页面点击 **添加 PDF** 选择一个或多个文件，页面会把它们放入 `BookScan/` 并自动开始扫描。也可以手动把新的 `.pdf` 文件放入 `BookScan/`，再点击 **扫描新增 PDF**。页面同时显示已扫描、待扫描 PDF、当前进度和词库内容。扫描过程中可以继续查看词库；完成后页面自动刷新。同名但内容不同的上传文件会自动改名保存，不会覆盖已有扫描件。

## 生成试题

先扫描 PDF，确认 `output/vocabulary.json` 中有足够且准确的词汇，并启动本地 AI。每次启动程序后，首次进入 **生成试题** 页会自动生成一份新卷；同一轮运行中刷新页面会保留当前卷，也可点击 **生成新试卷**。程序按 [出题规范](docs/KIDS_VOCAB_QUIZ_MAKER.md) 分题型调用本地 AI、校验返回的 JSON；中英配对直接从词库抽取原始释义。每份试卷固定 15 题，预计 15–20 分钟：热身认读 5 题（找不同类 1、特征配对 2、中英配对 2）、拼写巩固 5 题（补全字母 3、字母重组 2）、语境读写 5 题（词库填空 3、简短对话选答 2）。30%／40%／30% 用作预计作答时间分配，题数仍各 5。字母和选项由程序打乱。T8 看图选词与 T9 生图提示词保留在规范中；当前本地模型不提供生图接口，因此暂不纳入组卷。

试题页是家长或教师的审核界面，显示每题参考答案，不供学生在线作答。点击 **审核通过**，题目立即保存到 `output/approved_questions.json`，成为可跨次组卷使用的备选题。每条备选题的 `id` 是题目内容（不含试卷题号）的完整 SHA-256 哈希；相同内容只存一条，记录它出现过的试卷来源。点击 **不通过 · 重出此题**，程序只重新生成并替换这一题，其他题目保留。已通过题目也可退回重出；只有不再关联任何已审核试卷时，旧备选题才会移除。审核与勾选状态在刷新和重启后保留。到页面下方勾选所需备选题，再点击 **导出 A4 横版 PDF**；程序只收录勾选的题目，将不含参考答案的纸笔试卷保存到 `output/`，同时提供浏览器下载。横版采用双栏排版，英文题干与选项放大显示；可以勾选少于或多于 15 题，PDF 按实际题数排版。

原始试卷 JSON 保存在 `output/quizzes/`。新卷优先避开上一份试卷的答案词。程序把补全字母题限制为缺中间两个字母，把填空词库限制为答案加最多三个干扰词，并拒绝词库外的词及常见的不自然句子。词库、审核记录和 PDF 均只在本机处理。

## 数据文件与去重

- `output/vocabulary.json`：词库，沿用原有的英文、中文解释及 PDF/页码/图块来源结构。
- `output/scan_manifest.json`：本机扫描记录。每份 PDF 的完整 SHA-256 内容哈希是记录 ID；记录中保存该 PDF 的识别结果，供以后重建词库。
- `output/.cache/`：逐图块识别缓存。某个 PDF 内容变化需要重新扫描时，未变化的图块仍可复用缓存。
- `output/quizzes/`：本机保存的待审核组合试卷 JSON，包含答案与逐题重出后的内容。
- `output/approved_questions.json`：人工通过的备选题、内容 SHA-256 ID、来源与勾选状态，包含参考答案，仅供本机审核。已有 UUID 记录读取时自动迁移为内容哈希，保留勾选状态。
- `output/quiz-*.pdf`：从已勾选题目生成的 A4 横版学生试卷，不含参考答案。
- `logs/smart-vocab-quiz.log`：统一滚动日志。

再次扫描时，程序先计算 `BookScan/` 中每份 PDF 的哈希。记录中已有相同哈希的 PDF 不会再次调用模型，即使文件名变化或存在同内容副本。新哈希才会进入识别流程。词库按当前 `BookScan/` 中的文件重建：移走 PDF 后再点击扫描，其来源和仅由它提供的词条会从词库移除；扫描记录仍保留，日后放回相同内容的 PDF 可以直接复用。

本次升级会把已有的词库和图块缓存登记到扫描记录中，不重新识别已完成的扫描件。若 `vocabulary.json` 在扫描记录建立后被手工修改，程序会拒绝覆盖，请先核对修改与扫描记录，避免词条丢失。

图片识别可能误读模糊字迹，正式出题前应抽查词条。被遮盖或无法辨认的内容会被跳过。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m compileall -q smart-vocab-quiz.py src
python -m flake8 smart-vocab-quiz.py src tests
```

测试只使用临时 PDF 和模拟 AI，不依赖真实业务数据或本地模型服务。

## 项目结构与同步

- `smart-vocab-quiz.py`：本地网页入口，自动打开功能选择页。
- `web/`：HTML、CSS、JavaScript 页面；`src/smartvocabquiz/web_server.py` 提供本机 HTTP 接口。
- `src/smartvocabquiz/flows/`：扫描、组卷与 A4 PDF 导出工作流；`modules/`：PDF 切图、模型客户端、哈希记录及 JSON 存储。
- `tests/`：增量扫描、迁移、组卷、逐题审核、PDF 导出、网页接口和本地 AI 状态测试。
- `docs/`：项目约定、更新记录和配置说明。

代码与文档同步到 GitHub；扫描 PDF、词库、扫描记录、缓存、日志和本机 API Key 均只留在本地。更新记录见 [CHANGELOG](docs/CHANGELOG.md)，代码组织与日志约定见 [项目约定](docs/COMMON_PROJECT_SKILLS.md)。
