# SudoCode 图片生成接口

项目使用 `sudocode_image_generate.py` 直接调用 SudoCode 的 OpenAI 兼容图片接口：

- API 地址：`https://api.sudocode.chat/v1/images/generations`
- 模型：`gpt-image-2`
- 请求格式：`application/json`
- 图片数据：响应中的 `data[0].b64_json`

实现只依赖 Python 标准库，不需要安装额外包。

## 配置

在项目根目录的 `.env` 中填写 SudoCode API Key：

```env
SUDOCODE_API_BASE_URL=https://api.sudocode.chat/v1
SUDOCODE_API_KEY=sk-你的-SudoCode-API-Key
SUDOCODE_IMAGE_MODEL=gpt-image-2
SUDOCODE_API_TIMEOUT=600
```

## 直接输入提示词

在项目根目录运行：

```powershell
python .\src\standalone_tools\sudocode_image_generate.py --prompt "画一只坐在窗边的橘猫"
```

图片默认保存到项目根目录的 `image_api_outputs` 文件夹。

## 从文件读取提示词

```powershell
python .\src\standalone_tools\sudocode_image_generate.py --prompt-file .\prompt.txt
```

提示词文件使用 UTF-8 编码。

## 指定输出文件和图片参数

```powershell
python .\src\standalone_tools\sudocode_image_generate.py `
  --prompt "一座雨夜中的未来城市" `
  --size 1024x1024 `
  --quality high `
  --output .\image_api_outputs\city.png
```

尺寸中的分隔符必须使用小写字母 `x`，例如 `1024x1024`。

## 查看全部参数

```powershell
python .\src\standalone_tools\sudocode_image_generate.py --help
```

接口格式依据：[SudoCode gpt-image-2 图片 API 文档](https://sudocode.chat/docs/image-api)。
