# AgentHub Agent Hardening Baseline

记录日期：2026-07-28

## 后端测试

执行命令：

```powershell
$env:PYTHONPATH='.'
python -m pytest tests -q
```

有效基线结果：

- 收集并执行 37 个测试；
- 37 个通过；
- 0 个失败；
- 5 条 `datetime.utcnow()` 弃用警告。

首次执行时出现 4 个 setup error，均由 pytest 无权访问系统临时目录
`C:\Users\Lenovo\AppData\Local\Temp\pytest-of-Lenovo` 引起。将 `TEMP` 和 `TMP`
指向隔离工作区内的可写临时目录后，原命令覆盖的全部 37 个测试通过，因此该问题
归类为执行环境权限问题，而不是代码回归。

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

依赖重新安装后解析到 MCP SDK 1.27.2 和 NumPy 2.5.1。再次执行后端全量测试，
结果仍为 37 个通过、0 个失败，没有低于修改前基线。
