# 本地 AI 配置

本项目连接用户已经启动的 OpenAI 兼容视觉模型服务。它不会启动、停止或重新配置本地 AI 进程。

## 配置

从项目根目录的 `common.env.example` 复制出 `common.env`，填写：

```dotenv
LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1
LLAMACPP_MODEL=local-model
LLAMACPP_API_KEY=
```

`LLAMACPP_MODEL` 必须与服务的 `/v1/models` 返回的模型 ID 一致。服务要求认证时填写 API Key；该文件只留在本机。请求超时、输出长度、PDF 渲染比例等可在 `config.yaml` 调整。

## 状态与识别

网页的 AI 状态控件通过只读的 `/health` 和 `/v1/models` 检查服务与模型。可用时显示“AI 已启动”；服务未运行、认证失败或配置模型不可用时显示“AI 未启动”，点击首页功能按钮会看到启动提示。状态控件可手动重新检测，页面也会定期刷新。

扫描新增 PDF 时才向 `/v1/chat/completions` 发送图片识别请求。已登记 SHA-256 的 PDF 不会重复识别。扫描结果保存在 `output/vocabulary.json`，记录保存在 `output/scan_manifest.json`。

## 排查

1. 检查服务是否已在 `LLAMACPP_BASE_URL` 对应地址运行。
2. 检查 `LLAMACPP_MODEL` 是否与服务模型列表一致。
3. 如模型列表返回认证错误，检查 `LLAMACPP_API_KEY`。
4. 查看 `logs/smart-vocab-quiz.log` 中的扫描错误；日志不会记录 API Key。
