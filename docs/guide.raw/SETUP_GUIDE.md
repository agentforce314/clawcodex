# 环境配置指南

## 1. 激活虚拟环境

```bash
# 进入项目目录
cd /root/Claw-Codex

# 激活虚拟环境
source .venv/bin/activate

# 确认 Python 版本
python --version  # 应该显示 Python 3.11.x
```

## 2. 配置 GLM API Key

### 方式一：环境变量（推荐用于测试）

```bash
# 临时设置（当前会话有效）
export GLM_API_KEY="your_api_key_here"

# 永久设置（添加到 ~/.bashrc）
echo 'export GLM_API_KEY="your_api_key_here"' >> ~/.bashrc
source ~/.bashrc
```

### 方式二：.env 文件（推荐用于开发）

```bash
# 在项目根目录创建 .env 文件
cat > .env << 'EOF'
# GLM API Configuration
GLM_API_KEY=your_api_key_here
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_DEFAULT_MODEL=glm-4

# Optional: Other APIs
# ANTHROPIC_API_KEY=your_anthropic_key
# OPENAI_API_KEY=your_openai_key
EOF

# .env 文件已在 .gitignore 中，不会被提交到 Git
```

## 3. GLM API 信息

### API 端点
- **Base URL**: `https://open.bigmodel.cn/api/paas/v4`
- **认证方式**: Bearer Token (API Key)
- **文档**: https://open.bigmodel.cn/dev/api

### 可用模型
- `glm-4` - 最新的 GLM-4 模型（推荐）
- `glm-4-flash` - 快速版本
- `glm-3-turbo` - GLM-3 Turbo

### API 调用示例（Python）
```python
from zhipuai import ZhipuAI

client = ZhipuAI(api_key="your_api_key")

response = client.chat.completions.create(
    model="glm-4",
    messages=[
        {"role": "user", "content": "你好"}
    ]
)
print(response.choices[0].message.content)
```

## 4. 验证配置

### 测试环境变量
```bash
# 检查环境变量是否设置
echo $GLM_API_KEY

# 如果使用 .env 文件，Python 会自动加载
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('GLM_API_KEY'))"
```

## 5. 后续步骤

配置完成后，我会：
1. 创建 `requirements.txt` 和 `setup.py`
2. 安装依赖：`uv pip install -e .`
3. 创建配置文件：`~/.clawcodex/config.json`
4. 测试 GLM API 连接

---

## 常见问题

### Q: API Key 在哪里获取？
A: 访问 https://open.bigmodel.cn/ 注册账号后获取

### Q: 如何获取 API Key？
A:
1. 登录智谱开放平台
2. 进入「API 密钥」页面
3. 创建新的 API Key

### Q: 有免费额度吗？
A: 新用户通常有免费试用额度，具体查看官网说明

---

## 下一步

请按以下步骤操作：

1. ✅ **激活虚拟环境**：
   ```bash
   source .venv/bin/activate
   ```

2. ✅ **配置 API Key**（二选一）：
   - 方式一：`export GLM_API_KEY="your_key"`
   - 方式二：创建 `.env` 文件并写入

3. ✅ **告诉我已完成**，我会继续后续步骤
