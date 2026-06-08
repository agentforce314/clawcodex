"""Stability Gate — vibe coding 模式下的提交前稳定性门禁。

在每次提交前运行:
    python3 -m pytest tests/stability_gate/ -q --tb=short -x

分层结构（从快到慢）:
  Stage 1: 核心模块导入          16 个核心模块    ~4s
  Stage 2: CLI 烟雾测试          6 个 CLI 断言    ~9s
  Stage 3: REPL + Headless       类存在 + 选项构建  ~4s
  Stage 4: Agent/Conversation    序列化/多轮/Session ~2s
  Stage 5: 扩展组件              21 个扩展模块    ~3s
  Stage 6: 性能守卫              3 个性能断言     ~3s

不依赖外部 API，全部使用 stdlib + 代码内构造。
"""
