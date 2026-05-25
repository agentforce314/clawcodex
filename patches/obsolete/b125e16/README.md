# Obsolete Patches — b125e16

这些 patch 已从活跃 series 中移除，保留作为历史参考。

## 移除原因

这些 patch 针对的路径在上游（68dc3c5）中不存在，属于 ClawCodex 项目新增的模块：

- `src/api/` — 3 patches
- `src/capabilities/` — 6 patches
- `src/orchestrator/` — 33 patches

相关文件已通过 git commit 直接添加到项目代码中，不再需要通过 patch 管理。

## 目录结构

```
patches/obsolete/b125e16/
├── 0002.src.api.init..py.patch
├── 0003.src.api.orchestration.py.patch
├── 0004.src.api.query.py.patch
├── 0005.src.capabilities.PHASE1.SKELETON.txt.patch
├── 0006.src.capabilities.init..py.patch
├── 0007.src.capabilities.agent.protocol.py.patch
├── 0008.src.capabilities.context.protocol.py.patch
├── 0009.src.capabilities.provider.protocol.py.patch
├── 0010.src.capabilities.tool.protocol.py.patch
└── 0019-0051.src.orchestrator.*.patch   # 33 patches
```

## 保留参考

这些 patch 文件保留用于：
- 代码演进历史分析
- 变更追踪参考
- 如果将来需要回溯查看原始修改内容