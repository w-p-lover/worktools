# 图片接口调用说明

这份文档说明如何直接调用 `https://www.right.codes/draw` 的图片接口。

当前项目里已经有独立客户端脚本：

- `src/standalone_tools/image_api_client.py`

它可以直接调用下面 3 个接口：

- `POST /v1/images/generations`
- `POST /v1/images/edits`
- `POST /v1/chat/completions`

注意：

- 这里是直接请求 `https://www.right.codes/draw`
- 不需要再启动本地代理服务
- `API Key` 要使用 `right.codes` 提供的 key，不是 OpenAI 官方 key

## 1. 环境准备

确保本机已安装 Python 3。

建议先在项目根目录准备 `.env` 文件：

路径：

- `C:\Users\IT074\Documents\New project\.env`

内容示例：

```env
IMAGE_API_BASE_URL=https://www.right.codes/draw
IMAGE_API_KEY=你的RightCode接口Key
IMAGE_API_MODEL=gpt-image-2
```

说明：

- `IMAGE_API_BASE_URL` 只写基础前缀，不要再拼 `/v1/images/generations`
- `IMAGE_API_KEY` 填你自己的接口 key
- `IMAGE_API_MODEL` 按文档使用 `gpt-image-2`

启动服务器端：

```env
uvicorn src.standalone_tools.image_api_proxy_server:app --host 0.0.0.0 --port 8000 
```

## 2. 客户端脚本位置

脚本路径：

- `C:\Users\IT074\Documents\New project\src\standalone_tools\image_api_client.py`

进入项目目录：

```powershell
cd "C:\Users\IT074\Documents\New project"
```

## 3. 文生图

命令：

```powershell
python .\src\standalone_tools\image_api_client.py generate --prompt "画一个Sam在抖音直播间带货 Right Code 的图片"
```

作用：

- 调用 `POST /v1/images/generations`
- 把返回的 `b64_json` 或 `url` 自动保存为本地图片

如果接口生成较慢，可以先用较小尺寸测试：

```powershell
python .\src\standalone_tools\image_api_client.py generate --prompt "画一只猫" --size 1024x1024 --n 1
```

如果提示词有很多行，先写入文本文件，例如 `prompt.txt`：

```text
画一张直播间电商海报。

主体：
一名主播正在介绍 Right Code。

画面要求：
- 真实摄影风格
- 柔和布光
- 不要出现乱码文字
```

然后用 `--prompt-file` 调用：

```powershell
python .\src\standalone_tools\image_api_client.py generate --prompt-file .\prompt.txt
```

注意：`--prompt` 和 `--prompt-file` 二选一即可，不要同时传。

## 4. 图片编辑

命令：

```powershell
python .\src\standalone_tools\image_api_client.py edit --prompt "改成水彩画风" --image ".\input.png"
```

多行提示词也可以使用：

```powershell
python .\src\standalone_tools\image_api_client.py edit --prompt-file .\prompt.txt --image ".\input.png"
```

作用：

- 调用 `POST /v1/images/edits`
- 上传本地图片并返回编辑后的结果
- 支持保存接口返回的 `b64_json` 或 `url`

说明：

- `--image` 要填本地图片路径
- 支持常见图片格式，例如 `png`、`jpg`、`webp`

## 5. Chat Completions 兼容方式改图

命令：

```powershell
python .\src\standalone_tools\image_api_client.py chat-edit --prompt "改成水彩画风" --image ".\input.png"
```

作用：

- 调用 `POST /v1/chat/completions`
- 请求体按接口文档使用 `role + content[]`
- 从响应里的 markdown 图片内容中提取 base64 并保存到本地

## 6. 输出位置

默认会把结果保存到项目根目录下：

```text
image_api_outputs
```

也可以手动指定输出目录和文件名，例如：

```powershell
python .\src\standalone_tools\image_api_client.py generate --prompt "画一只猫" --output-dir ".\my_output" --filename "cat.png"
```

## 7. 不使用 `.env` 的写法

如果你不想使用 `.env`，也可以直接把参数写在命令里。

### 文生图

```powershell
python .\src\standalone_tools\image_api_client.py generate `
  --base-url "https://www.right.codes/draw" `
  --api-key "你的RightCode接口Key" `
  --model "gpt-image-2" `
  --prompt "画一只猫"
```

### 图片编辑

```powershell
python .\src\standalone_tools\image_api_client.py edit `
  --base-url "https://www.right.codes/draw" `
  --api-key "你的RightCode接口Key" `
  --model "gpt-image-2" `
  --prompt "改成水彩画风" `
  --image ".\input.png"
```

### Chat Completions 兼容改图

```powershell
python .\src\standalone_tools\image_api_client.py chat-edit `
  --base-url "https://www.right.codes/draw" `
  --api-key "你的RightCode接口Key" `
  --model "gpt-image-2" `
  --prompt "改成水彩画风" `
  --image ".\input.png"
```

## 8. 直接请求接口的示例

如果你不用 Python 脚本，也可以自己直接请求接口。

### 8.1 文生图

```powershell
curl -X POST "https://www.right.codes/draw/v1/images/generations" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer 你的RightCode接口Key" `
  -d "{\"model\":\"gpt-image-2\",\"prompt\":\"画一只猫\"}"
```

### 8.2 Chat Completions 兼容方式

请求体结构示意：

```json
{
  "model": "gpt-image-2",
  "role": "user",
  "content": [
    { "type": "text", "text": "改成水彩画风" },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,这里放base64"
      }
    }
  ]
}
```

## 9. 常见问题

### 9.1 `UnicodeEncodeError: 'latin-1' codec can't encode characters`

原因：

- 旧的请求写法把中文内容按错误编码发送了

当前 `image_api_client.py` 已经修过这个问题。

### 9.2 `401 invalid_api_key`

原因：

- 使用了错误的 key
- 把 OpenAI 的 key 当成了 `right.codes` 的 key

解决：

- 确认 `.env` 里的 `IMAGE_API_KEY` 是 `right.codes` 提供的 key

### 9.3 `10060` 或 `10061` 网络错误

原因：

- 地址不可达
- 端口不通
- 基础地址写错

解决：

- 确认 `.env` 中填写的是：

```env
IMAGE_API_BASE_URL=https://www.right.codes/draw
```

而不是本机 IP 或其他未启动的地址。

### 9.4 `524`

原因：

- 请求已经到达 `https://www.right.codes/draw`
- Right Code 后端生成图片耗时过长，入口网关等不到结果后返回超时

解决：

- 先用更短提示词、较小尺寸或 `--n 1` 测试
- 如果简单请求仍然返回 `524`，通常需要等待服务端恢复或联系 Right Code 服务方

## 10. 推荐调用顺序

推荐先从最简单的命令开始验证：

```powershell
python .\src\standalone_tools\image_api_client.py generate --prompt "画一只猫"
```

如果这条能成功，再继续试图片编辑和 `chat-edit`。
