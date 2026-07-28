# AgentHub Agent Hardening Baseline

记录日期：2026-07-28

## 后端测试

执行命令：

```powershell
$env:PYTHONPATH='.'
python -m pytest tests -q
```

有效基线结果：

- 收集并执行 96 个既有测试；
- 96 个通过；
- 0 个失败；
- 19 条 `datetime.utcnow()` 弃用警告。

仓库 `.gitignore` 忽略整个 `tests/` 目录，因此隔离 worktree 初始只包含已经被 Git
跟踪的测试文件。基线统计将原工作区中其余既有测试以本地硬链接挂入隔离 worktree
后得出，未把 Task 1 后新增的 8 个 Planner 测试计入基线。

首次执行时，pytest 无权访问系统临时目录
`C:\Users\Lenovo\AppData\Local\Temp\pytest-of-Lenovo`；隔离 worktree 也不会复制被
忽略的 `.env`，导致一个只构造 `ChatOpenAI`、不发起 live 请求的测试缺少凭据。
将 `TEMP` 和 `TMP` 指向隔离工作区内的可写临时目录，并为测试进程设置非真实的
`ALIYUN_API_KEY=test-key` 后，全部 96 个既有测试通过。这两项均归类为隔离执行
环境问题，而不是代码回归，且没有复制或记录真实密钥。

## 前端构建

执行命令：

```powershell
cd agenthub-frontend
npm run build
cd ..
```

结果：构建通过。TypeScript project build 与 Vite production build 均成功，
Vite 8.0.16 转换了 2,626 个模块并在 8.67 秒完成打包。构建报告一个非阻塞警告：
主 JavaScript chunk 压缩后为 1,183.68 kB，超过默认 500 kB 提示阈值。

## 依赖边界

- MCP Python SDK 固定为 `mcp[cli]>=1.27,<2`，保持当前 MCP SDK v1 API 兼容；
- NumPy 固定为 `numpy>=2.1,<3`，供后续本地向量相似度计算使用；
- 本轮不升级到 MCP SDK v2。

依赖重新安装后解析到 MCP SDK 1.27.2 和 NumPy 2.5.1。再次执行后端既有完整测试，
结果仍为 96 个通过、0 个失败，没有低于修改前基线。
